import json
import time
import threading
import requests
from datetime import datetime, timezone
from MyMQTT import MyMQTT

class FoodSpoilageControl:
    def __init__(self, settings_file="settings.json"):
        self.settings_file = settings_file
        self.settings = self.load_settings()
        
        # Service configuration from settings
        self.service_info = self.settings["serviceInfo"]
        self.service_id = self.service_info["serviceID"]
        self.catalog_url = self.settings["catalog"]["url"]
        
        # MQTT configuration
        self.mqtt_client = None
        self.broker_host = self.settings["mqtt"]["brokerIP"]
        self.broker_port = self.settings["mqtt"]["brokerPort"]
        self.connected = False
        
        # Device management
        self.last_alert_time = {}    # {device_id: timestamp} for cooldown tracking
        self.gas_status = {}         # {device_id: "normal"|"high"} for continuous alert logic
        self.known_devices = set()   # Cache of devices we know exist in catalog
        
        # Threading
        self.running = True
        self.config_lock = threading.RLock()  # RLock invece di Lock() per evitare deadlock
        
        print(f"[INIT] {self.service_id} service starting...")
    
    def load_settings(self):
        """Load settings from JSON file"""
        try:
            with open(self.settings_file, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            print(f"[ERROR] Settings file {self.settings_file} not found")
            raise
        except json.JSONDecodeError as e:
            print(f"[ERROR] Invalid JSON in settings file: {e}")
            raise
    
    def save_settings(self):
        """Save current settings to file"""
        print(f"[CONFIG] Starting save_settings()...")
        
        try:
            # Don't use config_lock here since we're already inside it
            self.settings["lastUpdate"] = datetime.now(timezone.utc).isoformat()
            self.settings["configVersion"] += 1
            
            print(f"[CONFIG] Updated metadata, writing to file {self.settings_file}...")
            
            with open(self.settings_file, 'w') as f:
                json.dump(self.settings, f, indent=4)
            print(f"[CONFIG] Settings saved to {self.settings_file}")
            
        except Exception as e:
            print(f"[CONFIG] ERROR in save_settings(): {e}")
            print(f"[CONFIG] Exception type: {type(e)}")
            import traceback
            traceback.print_exc()
    
    def extract_mqtt_topics(self):
        """Extract MQTT topics from service endpoints"""
        subscribe_topics = []
        publish_topics = []
        
        for endpoint in self.service_info["endpoints"]:
            if endpoint.startswith("MQTT Subscribe: "):
                topic = endpoint.replace("MQTT Subscribe: ", "")
                subscribe_topics.append(topic)
            elif endpoint.startswith("MQTT Publish: "):
                topic = endpoint.replace("MQTT Publish: ", "")
                publish_topics.append(topic)
        
        return subscribe_topics, publish_topics
    
    def register_with_catalog(self):
        """Register service with catalog via REST"""
        try:
            registration_data = {
                "serviceID": self.service_info["serviceID"],
                "name": self.service_info["serviceName"],
                "description": self.service_info["serviceDescription"],
                "type": self.service_info["serviceType"],
                "version": self.service_info["version"],
                "endpoints": self.service_info["endpoints"],
                "status": "active"
            }
            
            response = requests.post(
                f"{self.catalog_url}/services/register",
                json=registration_data,
                timeout=5
            )
            
            if response.status_code in [200, 201]:
                print(f"[REGISTER] Successfully registered with catalog")
                return True
            else:
                print(f"[REGISTER] Failed to register: {response.status_code}")
                print(f"[REGISTER] Response: {response.text}")
                return False
                
        except requests.RequestException as e:
            print(f"[REGISTER] Error registering with catalog: {e}")
            return False
    
    def check_device_exists_in_catalog(self, device_id):
        """Check if device exists in catalog via REST API"""
        try:
            response = requests.get(f"{self.catalog_url}/devices/{device_id}/exists", timeout=5)
            if response.status_code == 200:
                result = response.json()
                exists = result.get("exists", False)
                
                if exists:
                    self.known_devices.add(device_id)
                    # Auto-register device if not in our local config
                    if device_id not in self.settings["devices"]:
                        self.auto_register_device(device_id)
                    return True
                else:
                    print(f"[DEVICE_CHECK] Device {device_id} not found in catalog")
                    return False
            else:
                print(f"[DEVICE_CHECK] Error checking device {device_id}: {response.status_code}")
                return False
                
        except requests.RequestException as e:
            print(f"[DEVICE_CHECK] Error connecting to catalog: {e}")
            return False
    
    def auto_register_device(self, device_id):
        """Auto-register device with default settings"""
        with self.config_lock:
            if device_id not in self.settings["devices"]:
                self.settings["devices"][device_id] = {
                    "gas_threshold_ppm": self.settings["defaults"]["gas_threshold_ppm"],
                    "enable_continuous_alerts": self.settings["defaults"]["enable_continuous_alerts"],
                    "alert_cooldown_minutes": self.settings["defaults"]["alert_cooldown_minutes"]
                }
                self.save_settings()
                print(f"[AUTO-REG] Device {device_id} auto-registered with default config")
    
    def get_device_config(self, device_id):
        """Get configuration for specific device, with fallback to default"""
        with self.config_lock:
            device_config = self.settings["devices"].get(device_id, {})
            defaults = self.settings["defaults"]
            
            # Merge device config with defaults
            return {**defaults, **device_config}
    
    def update_device_config(self, device_id, new_config):
        """Update configuration for a specific device"""
        with self.config_lock:
            if device_id not in self.settings["devices"]:
                self.settings["devices"][device_id] = {}
            
            self.settings["devices"][device_id].update(new_config)
            self.save_settings()
            
            print(f"[CONFIG] Updated configuration for {device_id}: {new_config}")
    
    def handle_config_update(self, topic, payload):
        """Handle configuration update/get via MQTT with validation and access control"""
        try:
            message = json.loads(payload)
            
            # Extract requester from topic: Group17/SmartChill/FoodSpoilageControl/{requester}/config_update
            topic_parts = topic.split('/')
            if len(topic_parts) < 5:
                self.send_config_error("invalid_topic", "Invalid topic format", topic)
                return
            
            requester = topic_parts[3]  # The + part from the topic
            
            # Handle configuration GET requests
            if message.get("type") == "config_get":
                device_id = message.get("device_id")
                
                if device_id:
                    # Return device-specific config
                    if requester != "admin" and requester != device_id:
                        self.send_config_error("access_denied", 
                                            f"Device {requester} cannot read config for {device_id}", 
                                            topic, device_id)
                        return
                    
                    # Check if device exists (admin bypass this check)
                    if requester != "admin" and device_id not in self.known_devices:
                        if not self.check_device_exists_in_catalog(device_id):
                            self.send_config_error("device_not_found", 
                                                f"Device {device_id} not found in catalog", 
                                                topic, device_id)
                            return
                    
                    device_config = self.get_device_config(device_id)
                    self.send_config_data(device_id, "device_config", device_config, topic)
                    
                else:
                    # Return default config (admin only)
                    if requester != "admin":
                        self.send_config_error("access_denied", 
                                            "Only admin can read default configuration", 
                                            topic)
                        return
                    
                    defaults = self.settings["defaults"]
                    self.send_config_data(None, "default_config", defaults, topic)
            
            # Handle device configuration updates
            elif message.get("type") == "device_config_update":
                device_id = message.get("device_id")
                new_config = message.get("config", {})
                
                # Validate required fields
                if not device_id or not new_config:
                    self.send_config_error("missing_fields", "Missing device_id or config", topic, device_id)
                    return
                
                # Access control: admin can modify any device, others only their own
                if requester != "admin":
                    if requester != device_id:
                        self.send_config_error("access_denied", 
                                            f"Device {requester} cannot modify config for {device_id}", 
                                            topic, device_id)
                        return
                
                # Check if device exists (admin bypass this check)
                if requester != "admin":
                    if device_id not in self.known_devices:
                        # Try to verify with catalog
                        if not self.check_device_exists_in_catalog(device_id):
                            self.send_config_error("device_not_found", 
                                                f"Device {device_id} not found in catalog", 
                                                topic, device_id)
                            return
                
                # Validate configuration values
                validation_error = self.validate_config_values(new_config)
                if validation_error:
                    self.send_config_error("invalid_config", validation_error, topic, device_id)
                    return
                
                # Apply configuration
                self.update_device_config(device_id, new_config)
                
                # Send success acknowledgment
                self.send_config_ack(device_id, "device_updated", new_config, topic)
                print(f"[CONFIG] Device config updated for {device_id} by {requester}")
                
            # Handle default configuration updates (admin only)
            elif message.get("type") == "default_config_update":
                new_config = message.get("config", {})
                
                if requester != "admin":
                    self.send_config_error("access_denied", 
                                        "Only admin can modify default configuration", 
                                        topic)
                    return
                
                if not new_config:
                    self.send_config_error("missing_fields", "Missing config", topic)
                    return
                
                # Validate configuration values
                validation_error = self.validate_config_values(new_config)
                if validation_error:
                    self.send_config_error("invalid_config", validation_error, topic)
                    return
                
                # Update defaults
                with self.config_lock:
                    self.settings["defaults"].update(new_config)
                    self.save_settings()
                
                # Send success acknowledgment
                self.send_config_ack(None, "defaults_updated", new_config, topic)
                print(f"[CONFIG] Default config updated by {requester}")
                
            else:
                self.send_config_error("unknown_type", f"Unknown config type: {message.get('type')}", topic)
                
        except json.JSONDecodeError as e:
            self.send_config_error("invalid_json", f"Invalid JSON payload: {str(e)}", topic)
        except Exception as e:
            self.send_config_error("internal_error", f"Internal error: {str(e)}", topic)
            print(f"[CONFIG] Unexpected error: {e}")

    def send_config_data(self, device_id, data_type, config, original_topic):
        """Send configuration data response"""
        if not self.connected or not self.mqtt_client:
            return
        
        # Extract requester from original topic
        topic_parts = original_topic.split('/')
        requester = topic_parts[3] if len(topic_parts) > 4 else "unknown"
        
        # Send data to requester-specific topic
        data_topic = f"Group17/SmartChill/FoodSpoilageControl/{requester}/config_data"
        data_payload = {
            "device_id": device_id,
            "data_type": data_type,
            "config": config,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "config_version": self.settings["configVersion"],
            "original_topic": original_topic
        }
        
        try:
            self.mqtt_client.myPublish(data_topic, data_payload)
            print(f"[CONFIG] Sent config data: {data_type} for {device_id or 'defaults'}")
        except Exception as e:
            print(f"[CONFIG] Error sending config data: {e}")

    def validate_config_values(self, config):
        """Validate configuration values and return error message if invalid"""
        
        # Check gas_threshold_ppm
        if "gas_threshold_ppm" in config:
            value = config["gas_threshold_ppm"]
            if not isinstance(value, int) or value < 100 or value > 1000:
                return "gas_threshold_ppm must be an integer between 100 and 1000"
        
        # Check enable_continuous_alerts
        if "enable_continuous_alerts" in config:
            value = config["enable_continuous_alerts"]
            if not isinstance(value, bool):
                return "enable_continuous_alerts must be a boolean (true/false)"
        
        # Check alert_cooldown_minutes
        if "alert_cooldown_minutes" in config:
            value = config["alert_cooldown_minutes"]
            if not isinstance(value, int) or value < 5 or value > 120:
                return "alert_cooldown_minutes must be an integer between 5 and 120"
        
        # Check for unknown config keys
        allowed_keys = {"gas_threshold_ppm", "enable_continuous_alerts", "alert_cooldown_minutes"}
        unknown_keys = set(config.keys()) - allowed_keys
        if unknown_keys:
            return f"Unknown configuration keys: {', '.join(unknown_keys)}"
        
        return None  # No validation errors

    def send_config_ack(self, device_id, status, config, original_topic):
        """Send positive configuration acknowledgment"""
        if not self.connected or not self.mqtt_client:
            return
        
        # Extract requester from original topic
        topic_parts = original_topic.split('/')
        requester = topic_parts[3] if len(topic_parts) > 4 else "unknown"
        
        # Send response to requester-specific topic
        ack_topic = f"Group17/SmartChill/FoodSpoilageControl/{requester}/config_ack"
        ack_payload = {
            "device_id": device_id,
            "status": status,
            "config": config,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "config_version": self.settings["configVersion"],
            "original_topic": original_topic
        }
        
        try:
            self.mqtt_client.myPublish(ack_topic, ack_payload)
            print(f"[CONFIG] Sent ACK: {status} for {device_id or 'defaults'}")
        except Exception as e:
            print(f"[CONFIG] Error sending ACK: {e}")

    def send_config_error(self, error_code, error_message, original_topic, device_id=None):
        """Send configuration error response"""
        if not self.connected or not self.mqtt_client:
            return
        
        # Extract requester from original topic
        topic_parts = original_topic.split('/')
        requester = topic_parts[3] if len(topic_parts) > 4 else "unknown"
        
        # Send error to requester-specific topic
        error_topic = f"Group17/SmartChill/FoodSpoilageControl/{requester}/config_error"
        error_payload = {
            "device_id": device_id,
            "error_code": error_code,
            "error_message": error_message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "original_topic": original_topic
        }
        
        try:
            self.mqtt_client.myPublish(error_topic, error_payload)
            print(f"[CONFIG] Sent ERROR: {error_code} - {error_message}")
        except Exception as e:
            print(f"[CONFIG] Error sending error response: {e}")
    
    def is_cooldown_active(self, device_id):
        """Check if device is in alert cooldown period"""
        if device_id not in self.last_alert_time:
            return False
        
        config = self.get_device_config(device_id)
        cooldown_minutes = config["alert_cooldown_minutes"]
        cooldown_seconds = cooldown_minutes * 60
        
        time_since_last_alert = time.time() - self.last_alert_time[device_id]
        return time_since_last_alert < cooldown_seconds
    
    def handle_gas_reading(self, device_id, gas_value, timestamp):
        """Handle incoming gas sensor reading"""
        config = self.get_device_config(device_id)
        threshold = config["gas_threshold_ppm"]
        enable_continuous = config["enable_continuous_alerts"]
        
        current_status = "high" if gas_value > threshold else "normal"
        previous_status = self.gas_status.get(device_id, "normal")
        
        # Update gas status
        self.gas_status[device_id] = current_status
        
        print(f"[GAS] {device_id}: {gas_value} PPM (threshold: {threshold} PPM) - Status: {current_status}")
        
        # Check if we should send an alert
        should_alert = False
        
        if current_status == "high":
            if enable_continuous:
                # Send alert if not in cooldown
                if not self.is_cooldown_active(device_id):
                    should_alert = True
            else:
                # Send alert only on transition from normal to high
                if previous_status == "normal":
                    should_alert = True
        
        if should_alert:
            self.send_spoilage_alert(device_id, gas_value, threshold, timestamp)
    
    def send_spoilage_alert(self, device_id, gas_value, threshold, timestamp):
        """Send food spoilage alert via MQTT"""
        if not self.connected or not self.mqtt_client:
            print(f"[ALERT] Cannot send spoilage alert - MQTT not connected")
            return
        
        config = self.get_device_config(device_id)
        
        alert_topic = f"Group17/SmartChill/{device_id}/Alerts/Spoilage"
        alert_payload = {
            "alert_type": "food_spoilage",
            "device_id": device_id,
            "message": f"High gas levels detected: {gas_value} PPM (threshold: {threshold} PPM). Possible food spoilage.",
            "gas_level_ppm": gas_value,
            "threshold_ppm": threshold,
            "over_threshold_by": gas_value - threshold,
            "severity": config.get("alert_severity", "warning"),
            "timestamp": timestamp.isoformat() if isinstance(timestamp, datetime) else timestamp,
            "service": self.service_id,
            "config_version": self.settings["configVersion"],
            "recommended_action": "Check fridge contents for spoiled food"
        }
        
        try:
            self.mqtt_client.myPublish(alert_topic, alert_payload)
            self.last_alert_time[device_id] = time.time()
            print(f"[ALERT] Spoilage alert sent for {device_id} - Gas: {gas_value} PPM > {threshold} PPM")
        except Exception as e:
            print(f"[ALERT] Error sending spoilage alert: {e}")
    
    def parse_senml_payload(self, payload):
        """Parse SenML formatted payload and extract sensor data"""
        try:
            # Decode se payload è bytes
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8")

            # Parse JSON
            senml_data = json.loads(payload) if isinstance(payload, str) else payload
            
            # Validate SenML structure
            if not isinstance(senml_data, dict) or "e" not in senml_data:
                print(f"[SENML] Invalid SenML structure - missing 'e' array")
                return None
            
            base_name = senml_data.get("bn", "")
            base_time = senml_data.get("bt", 0)
            entries = senml_data.get("e", [])
            
            # Extract device_id from base_name (format: "device_id/")
            device_id = base_name.rstrip("/") if base_name.endswith("/") else None
            
            parsed_data = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                
                sensor_name = entry.get("n")
                value = entry.get("v")
                time_offset = entry.get("t", 0)
                timestamp = base_time + time_offset
                
                if sensor_name and value is not None:
                    parsed_data.append({
                        "device_id": device_id,
                        "sensor_name": sensor_name,
                        "value": value,
                        "timestamp": timestamp
                    })
            
            return parsed_data
            
        except (json.JSONDecodeError, TypeError) as e:
            print(f"[SENML] Error parsing SenML payload: {e}")
            return None
    
    def notify(self, topic, payload):
        """Callback method for MyMQTT - handles incoming SenML gas sensor data"""
        try:
            # Handle configuration updates
            if "config_update" in topic:
                self.handle_config_update(topic, payload)
                return
            
            # Parse SenML payload
            parsed_data = self.parse_senml_payload(payload)
            if not parsed_data:
                print(f"[SENML] Failed to parse SenML data from topic: {topic}")
                return
            
            topic_parts = topic.split('/')
            
            # Expected topic: Group17/SmartChill/Devices/{model}/{device_id}/gas
            if len(topic_parts) >= 5 and topic_parts[-1] == "gas":
                topic_device_id = topic_parts[-2]  # Extract device_id from topic
                
                # Process each entry in the SenML payload
                for data_entry in parsed_data:
                    device_id = data_entry["device_id"] or topic_device_id
                    sensor_name = data_entry["sensor_name"]
                    value = data_entry["value"]
                    timestamp = data_entry["timestamp"]
                    
                    # Only process gas sensor data
                    if sensor_name != "gas":
                        continue
                    
                    # Check if we know this device - if not, verify with catalog
                    if device_id not in self.known_devices:
                        print(f"[NEW_DEVICE] Unknown device detected: {device_id}")
                        if self.check_device_exists_in_catalog(device_id):
                            print(f"[NEW_DEVICE] Device {device_id} confirmed in catalog")
                        else:
                            print(f"[NEW_DEVICE] Device {device_id} not registered in catalog - ignoring data")
                            continue
                    
                    # Convert timestamp to datetime
                    if timestamp:
                        try:
                            ts = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                        except (ValueError, TypeError):
                            ts = datetime.now(timezone.utc)
                    else:
                        ts = datetime.now(timezone.utc)
                    
                    # Process gas reading
                    self.handle_gas_reading(device_id, float(value), ts)
                    print(f"[SENML] Processed gas data: {device_id} = {value} PPM")
            else:
                print(f"[WARN] Unexpected topic format: {topic}")
                
        except Exception as e:
            print(f"[ERROR] Error processing SenML message: {e}")
            import traceback
            traceback.print_exc()
    
    def setup_mqtt(self):
        """Setup MQTT client and subscribe to topics from service endpoints"""
        try:
            client_id = f"{self.settings['mqtt']['clientID_prefix']}_{int(time.time())}"
            self.mqtt_client = MyMQTT(client_id, self.broker_host, self.broker_port, self)
            
            # Start connection
            self.mqtt_client.start()
            time.sleep(2)
            self.connected = True
            
            # Extract and subscribe to topics from service endpoints
            subscribe_topics, _ = self.extract_mqtt_topics()
            for topic in subscribe_topics:
                self.mqtt_client.mySubscribe(topic)
                print(f"[MQTT] Subscribed to: {topic}")
            
            print(f"[MQTT] Connected to broker {self.broker_host}:{self.broker_port}")
            return True
            
        except Exception as e:
            print(f"[MQTT] Connection error: {e}")
            return False
    
    def load_known_devices_from_catalog(self):
        """Load all registered devices from catalog at startup"""
        try:
            response = requests.get(f"{self.catalog_url}/devices", timeout=5)
            if response.status_code == 200:
                devices = response.json()
                
                for device in devices:
                    device_id = device.get("deviceID")
                    if device_id and device_id.startswith("SmartChill_"):
                        self.known_devices.add(device_id)
                        
                        # Auto-register if not in local config
                        if device_id not in self.settings["devices"]:
                            self.auto_register_device(device_id)
                
                print(f"[INIT] Loaded {len(self.known_devices)} known devices from catalog")
                return True
            else:
                print(f"[INIT] Failed to load devices from catalog: {response.status_code}")
                return False
                
        except requests.RequestException as e:
            print(f"[INIT] Error loading devices from catalog: {e}")
            return False
    
    def periodic_registration(self):
        """Periodically re-register with catalog"""
        interval = self.settings["catalog"]["registration_interval_seconds"]
        
        while self.running:
            time.sleep(interval)
            if self.running:
                print(f"[REGISTER] Periodic re-registration...")
                self.register_with_catalog()
    
    def status_monitor_loop(self):
        """Monitor service status and show current gas status"""
        while self.running:
            try:
                # Show current gas status for devices
                if self.gas_status:
                    print(f"[STATUS] Current gas status:")
                    for device_id, status in self.gas_status.items():
                        config = self.get_device_config(device_id)
                        threshold = config["gas_threshold_ppm"]
                        cooldown_active = self.is_cooldown_active(device_id)
                        cooldown_str = " (COOLDOWN)" if cooldown_active else ""
                        print(f"  {device_id}: {status} (threshold: {threshold} PPM){cooldown_str}")
                
                # Sleep for ping interval
                time.sleep(self.settings["catalog"]["ping_interval_seconds"])
                
            except Exception as e:
                print(f"[STATUS] Error in status monitor: {e}")
                time.sleep(30)
    
    def get_status(self):
        """Get current service status"""
        return {
            "service_id": self.service_id,
            "status": "running" if self.running else "stopped",
            "mqtt_connected": self.connected,
            "known_devices": len(self.known_devices),
            "monitored_devices": len(self.gas_status),
            "devices_in_cooldown": len([d for d in self.known_devices if self.is_cooldown_active(d)]),
            "config_version": self.settings["configVersion"]
        }
    
    def run(self):
        """Main run method"""
        print("=" * 60)
        print("    SMARTCHILL FOOD SPOILAGE CONTROL SERVICE")
        print("=" * 60)
        
        # Step 1: Register with catalog
        print("[INIT] Registering service with catalog...")
        if not self.register_with_catalog():
            print("[WARN] Failed to register with catalog - continuing anyway")
        
        # Step 2: Load known devices from catalog
        print("[INIT] Loading known devices from catalog...")
        self.load_known_devices_from_catalog()
        
        # Step 3: Setup MQTT connection
        print("[INIT] Setting up MQTT connection...")
        if not self.setup_mqtt():
            print("[ERROR] Failed to setup MQTT connection")
            return
        
        print(f"[INIT] Service started successfully!")
        print(f"[INIT] Monitoring {len(self.settings['devices'])} configured devices")
        print(f"[INIT] Known devices from catalog: {len(self.known_devices)}")
        print(f"[INIT] Default gas threshold: {self.settings['defaults']['gas_threshold_ppm']} PPM")
        
        # Start background threads (no monitoring loop needed - event-driven)
        registration_thread = threading.Thread(target=self.periodic_registration, daemon=True)
        status_thread = threading.Thread(target=self.status_monitor_loop, daemon=True)
        
        registration_thread.start()
        status_thread.start()
        
        # Main loop - keep service alive
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[SHUTDOWN] Received interrupt signal...")
            self.shutdown()
    
    def shutdown(self):
        """Graceful shutdown"""
        print("[SHUTDOWN] Stopping Food Spoilage Control service...")
        self.running = False
        
        if self.mqtt_client:
            try:
                self.mqtt_client.stop()
                print("[SHUTDOWN] MQTT connection closed")
            except Exception as e:
                print(f"[SHUTDOWN] Error closing MQTT: {e}")
        
        print("[SHUTDOWN] Food Spoilage Control service stopped")

def main():
    """Main entry point"""
    service = FoodSpoilageControl()
    
    try:
        service.run()
    except Exception as e:
        print(f"[FATAL] Service error: {e}")
    finally:
        service.shutdown()

if __name__ == "__main__":
    main()