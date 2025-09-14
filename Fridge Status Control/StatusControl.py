import json
import time
import threading
import requests
from datetime import datetime, timezone
from MyMQTT import MyMQTT

class FridgeStatusControl:
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
        
        # Device management and status tracking
        self.last_alert_time = {}           # {device_id: {alert_type: timestamp}} for cooldown tracking
        self.device_status = {}             # {device_id: {"temp_status": "normal"|"high"|"low", "humidity_status": "normal"|"high"}}
        self.known_devices = set()          # Cache of devices we know exist in catalog
        self.last_readings = {}             # {device_id: {"temperature": value, "humidity": value, "timestamp": ts}}
        
        # Threading
        self.running = True
        self.config_lock = threading.RLock()
        
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
        try:
            self.settings["lastUpdate"] = datetime.now(timezone.utc).isoformat()
            self.settings["configVersion"] += 1
            
            with open(self.settings_file, 'w') as f:
                json.dump(self.settings, f, indent=4)
            print(f"[CONFIG] Settings saved to {self.settings_file}")
            
        except Exception as e:
            print(f"[CONFIG] Error in save_settings(): {e}")
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
                    "temp_min_celsius": self.settings["defaults"]["temp_min_celsius"],
                    "temp_max_celsius": self.settings["defaults"]["temp_max_celsius"],
                    "humidity_max_percent": self.settings["defaults"]["humidity_max_percent"],
                    "enable_malfunction_alerts": self.settings["defaults"]["enable_malfunction_alerts"],
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
        """Handle configuration update via MQTT"""
        try:
            message = json.loads(payload)
            
            if message.get("type") == "device_config_update":
                device_id = message.get("device_id")
                new_config = message.get("config", {})
                
                if device_id and new_config:
                    self.update_device_config(device_id, new_config)
                    
                    # Acknowledge the update
                    ack_topic = f"Group17/SmartChill/FridgeStatusControl/config_ack"
                    ack_payload = {
                        "device_id": device_id,
                        "status": "updated",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "config_version": self.settings["configVersion"]
                    }
                    self.mqtt_client.myPublish(ack_topic, ack_payload)
                    
        except Exception as e:
            print(f"[CONFIG] Error processing config update: {e}")
    
    def is_cooldown_active(self, device_id, alert_type):
        """Check if device is in alert cooldown period for specific alert type"""
        if device_id not in self.last_alert_time or alert_type not in self.last_alert_time[device_id]:
            return False
        
        config = self.get_device_config(device_id)
        cooldown_minutes = config["alert_cooldown_minutes"]
        cooldown_seconds = cooldown_minutes * 60
        
        time_since_last_alert = time.time() - self.last_alert_time[device_id][alert_type]
        return time_since_last_alert < cooldown_seconds
    
    def analyze_temperature_status(self, device_id, temperature, timestamp):
        """Analyze temperature reading and detect anomalies"""
        config = self.get_device_config(device_id)
        temp_min = config["temp_min_celsius"]
        temp_max = config["temp_max_celsius"]
        
        # Determine temperature status
        if temperature < temp_min:
            status = "too_low"
            alert_type = "temperature_too_low"
            message = f"Temperature too low: {temperature:.1f}°C (min: {temp_min}°C). Risk of freezing food items."
            recommended_action = "Check thermostat settings and increase temperature"
            severity = "warning"
        elif temperature > temp_max:
            status = "too_high"
            alert_type = "temperature_too_high"
            message = f"Temperature too high: {temperature:.1f}°C (max: {temp_max}°C). Risk of food spoilage."
            recommended_action = "Check thermostat settings, door seals, and reduce temperature"
            severity = "critical"
        else:
            status = "normal"
            alert_type = None
            message = None
            recommended_action = None
            severity = None
        
        # Update device status
        if device_id not in self.device_status:
            self.device_status[device_id] = {}
        
        previous_temp_status = self.device_status[device_id].get("temp_status", "normal")
        self.device_status[device_id]["temp_status"] = status
        
        print(f"[TEMP] {device_id}: {temperature:.1f}°C (range: {temp_min}-{temp_max}°C) - Status: {status}")
        
        # Send alert if needed
        if alert_type and config["enable_malfunction_alerts"]:
            # Send alert only on status change or if continuous alerts enabled
            if previous_temp_status == "normal" or not self.is_cooldown_active(device_id, alert_type):
                self.send_malfunction_alert(device_id, alert_type, message, temperature, severity, recommended_action, timestamp)
    
    def analyze_humidity_status(self, device_id, humidity, timestamp):
        """Analyze humidity reading and detect anomalies"""
        config = self.get_device_config(device_id)
        humidity_max = config["humidity_max_percent"]
        
        # Determine humidity status
        if humidity > humidity_max:
            status = "too_high"
            alert_type = "humidity_too_high"
            message = f"Humidity too high: {humidity:.1f}% (max: {humidity_max}%). Risk of ice formation and condensation."
            recommended_action = "Check door seals, defrost if needed, ensure proper air circulation"
            severity = "warning"
        else:
            status = "normal"
            alert_type = None
            message = None
            recommended_action = None
            severity = None
        
        # Update device status
        if device_id not in self.device_status:
            self.device_status[device_id] = {}
        
        previous_humidity_status = self.device_status[device_id].get("humidity_status", "normal")
        self.device_status[device_id]["humidity_status"] = status
        
        print(f"[HUMIDITY] {device_id}: {humidity:.1f}% (max: {humidity_max}%) - Status: {status}")
        
        # Send alert if needed
        if alert_type and config["enable_malfunction_alerts"]:
            # Send alert only on status change or if not in cooldown
            if previous_humidity_status == "normal" or not self.is_cooldown_active(device_id, alert_type):
                self.send_malfunction_alert(device_id, alert_type, message, humidity, severity, recommended_action, timestamp)
    
    def detect_malfunction_patterns(self, device_id):
        """Detect complex malfunction patterns based on combined sensor data"""
        if device_id not in self.last_readings:
            return
        
        readings = self.last_readings[device_id]
        temperature = readings.get("temperature")
        humidity = readings.get("humidity")
        
        if temperature is None or humidity is None:
            return
        
        config = self.get_device_config(device_id)
        
        # Detect specific malfunction patterns
        if temperature > config["temp_max_celsius"] and humidity > config["humidity_max_percent"]:
            # Both temp and humidity high - possible cooling system failure
            alert_type = "cooling_system_failure"
            message = f"Possible cooling system failure: High temperature ({temperature:.1f}°C) and humidity ({humidity:.1f}%)"
            recommended_action = "Check cooling system, compressor, and refrigerant levels. Contact technician if needed."
            severity = "critical"
            
            if not self.is_cooldown_active(device_id, alert_type):
                self.send_malfunction_alert(device_id, alert_type, message, 
                                          {"temperature": temperature, "humidity": humidity}, 
                                          severity, recommended_action, readings["timestamp"])
        
        elif temperature < config["temp_min_celsius"] and humidity > config["humidity_max_percent"]:
            # Low temp but high humidity - possible defrost cycle issue
            alert_type = "defrost_cycle_issue"
            message = f"Possible defrost cycle issue: Low temperature ({temperature:.1f}°C) with high humidity ({humidity:.1f}%)"
            recommended_action = "Check defrost cycle settings and drainage system"
            severity = "warning"
            
            if not self.is_cooldown_active(device_id, alert_type):
                self.send_malfunction_alert(device_id, alert_type, message, 
                                          {"temperature": temperature, "humidity": humidity}, 
                                          severity, recommended_action, readings["timestamp"])
    
    def send_malfunction_alert(self, device_id, alert_type, message, sensor_value, severity, recommended_action, timestamp):
        """Send malfunction alert via MQTT"""
        if not self.connected or not self.mqtt_client:
            print(f"[ALERT] Cannot send malfunction alert - MQTT not connected")
            return
        
        alert_topic = f"Group17/SmartChill/{device_id}/Alerts/Malfunction"
        alert_payload = {
            "alert_type": alert_type,
            "device_id": device_id,
            "message": message,
            "sensor_values": sensor_value if isinstance(sensor_value, dict) else {alert_type.split('_')[0]: sensor_value},
            "severity": severity,
            "timestamp": timestamp.isoformat() if isinstance(timestamp, datetime) else timestamp,
            "service": self.service_id,
            "config_version": self.settings["configVersion"],
            "recommended_action": recommended_action
        }
        
        try:
            self.mqtt_client.myPublish(alert_topic, alert_payload)
            
            # Update cooldown tracking
            if device_id not in self.last_alert_time:
                self.last_alert_time[device_id] = {}
            self.last_alert_time[device_id][alert_type] = time.time()
            
            print(f"[ALERT] Malfunction alert sent for {device_id}: {alert_type}")
        except Exception as e:
            print(f"[ALERT] Error sending malfunction alert: {e}")
    
    def parse_senml_payload(self, payload):
        """Parse SenML formatted payload and extract sensor data"""
        try:
            # Decode if payload is bytes
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
        """Callback method for MyMQTT - handles incoming SenML sensor data"""
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
            
            # Expected topics: 
            # Group17/SmartChill/Devices/{model}/{device_id}/temperature
            # Group17/SmartChill/Devices/{model}/{device_id}/humidity
            if len(topic_parts) >= 5 and topic_parts[-1] in ["temperature", "humidity"]:
                topic_device_id = topic_parts[-2]  # Extract device_id from topic
                sensor_type = topic_parts[-1]      # temperature or humidity
                
                # Process each entry in the SenML payload
                for data_entry in parsed_data:
                    device_id = data_entry["device_id"] or topic_device_id
                    sensor_name = data_entry["sensor_name"]
                    value = data_entry["value"]
                    timestamp = data_entry["timestamp"]
                    
                    # Only process temperature and humidity sensor data
                    if sensor_name not in ["temperature", "humidity"]:
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
                    
                    # Store reading for pattern detection
                    if device_id not in self.last_readings:
                        self.last_readings[device_id] = {}
                    
                    self.last_readings[device_id][sensor_name] = float(value)
                    self.last_readings[device_id]["timestamp"] = ts
                    
                    # Process sensor reading
                    if sensor_name == "temperature":
                        self.analyze_temperature_status(device_id, float(value), ts)
                    elif sensor_name == "humidity":
                        self.analyze_humidity_status(device_id, float(value), ts)
                    
                    print(f"[SENML] Processed {sensor_name} data: {device_id} = {value}")
                
                # After processing individual sensors, check for malfunction patterns
                for data_entry in parsed_data:
                    device_id = data_entry["device_id"] or topic_device_id
                    if device_id in self.known_devices:
                        self.detect_malfunction_patterns(device_id)
                        break  # Only check once per message
                        
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
        """Monitor service status and show current device status"""
        while self.running:
            try:
                # Show current device status
                if self.device_status:
                    print(f"[STATUS] Current device status:")
                    for device_id, status in self.device_status.items():
                        config = self.get_device_config(device_id)
                        temp_status = status.get("temp_status", "unknown")
                        humidity_status = status.get("humidity_status", "unknown")
                        
                        temp_range = f"{config['temp_min_celsius']}-{config['temp_max_celsius']}°C"
                        humidity_max = f"<{config['humidity_max_percent']}%"
                        
                        print(f"  {device_id}: temp={temp_status} ({temp_range}), humidity={humidity_status} ({humidity_max})")
                
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
            "monitored_devices": len(self.device_status),
            "devices_with_alerts": len([d for d in self.device_status.keys() 
                                      if self.device_status[d].get("temp_status") != "normal" 
                                      or self.device_status[d].get("humidity_status") != "normal"]),
            "config_version": self.settings["configVersion"]
        }
    
    def run(self):
        """Main run method"""
        print("=" * 60)
        print("    SMARTCHILL FRIDGE STATUS CONTROL SERVICE")
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
        print(f"[INIT] Default temp range: {self.settings['defaults']['temp_min_celsius']}-{self.settings['defaults']['temp_max_celsius']}°C")
        print(f"[INIT] Default humidity max: {self.settings['defaults']['humidity_max_percent']}%")
        
        # Start background threads
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
        print("[SHUTDOWN] Stopping Fridge Status Control service...")
        self.running = False
        
        if self.mqtt_client:
            try:
                self.mqtt_client.stop()
                print("[SHUTDOWN] MQTT connection closed")
            except Exception as e:
                print(f"[SHUTDOWN] Error closing MQTT: {e}")
        
        print("[SHUTDOWN] Fridge Status Control service stopped")

def main():
    """Main entry point"""
    service = FridgeStatusControl()
    
    try:
        service.run()
    except Exception as e:
        print(f"[FATAL] Service error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        service.shutdown()

if __name__ == "__main__":
    main()