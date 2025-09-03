import json
import time
import threading
import requests
import random
from datetime import datetime, timezone
from MyMQTT import MyMQTT

class TimerUsageControl:
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
        self.device_timers = {}      # {device_id: start_time}
        self.alerted_devices = {}    # {device_id: alert_sent_time}
        self.known_devices = set()   # Cache of devices we know exist in catalog
        
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
        with self.config_lock:
            self.settings["lastUpdate"] = datetime.now(timezone.utc).isoformat()
            self.settings["configVersion"] += 1
            
            try:
                with open(self.settings_file, 'w') as f:
                    json.dump(self.settings, f, indent=4)
                print(f"[CONFIG] Settings saved to {self.settings_file}")
            except Exception as e:
                print(f"[ERROR] Failed to save settings: {e}")
    
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
    
    def register_with_catalog(self, max_retries=5, base_delay=2):
        """Register service with catalog via REST with retry logic"""
        for attempt in range(max_retries):
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
                    print(f"[REGISTER] Failed to register (attempt {attempt+1}/{max_retries}): {response.status_code}")
                    
            except requests.RequestException as e:
                print(f"[REGISTER] Error registering (attempt {attempt+1}/{max_retries}): {e}")
            
            if attempt < max_retries - 1:  # Don't sleep on last attempt
                delay = base_delay * (2 ** attempt) + random.uniform(0, 1)  # Exponential backoff with jitter
                print(f"[REGISTER] Retrying in {delay:.1f} seconds...")
                time.sleep(delay)
        
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
                    "max_door_open_seconds": self.settings["defaults"]["max_door_open_seconds"],
                    "check_interval": self.settings["defaults"]["check_interval"],
                    "enable_door_closed_alerts": True
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
                    ack_topic = f"Group17/SmartChill/TimerUsageControl/config_ack"
                    ack_payload = {
                        "device_id": device_id,
                        "status": "updated",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "config_version": self.settings["configVersion"]
                    }
                    self.mqtt_client.myPublish(ack_topic, ack_payload)
                    
        except Exception as e:
            print(f"[CONFIG] Error processing config update: {e}")
    
    def parse_senml_payload(self, payload):
        """Parse SenML formatted payload and extract door event data"""
        try:
            # Parse JSON payload
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
            
            door_events = {}
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                
                sensor_name = entry.get("n")
                time_offset = entry.get("t", 0)
                timestamp = base_time + time_offset
                
                # Handle door state events
                if sensor_name == "door_state":
                    door_events["event_type"] = entry.get("vs")  # String value
                    door_events["timestamp"] = timestamp
                    door_events["device_id"] = device_id
                
                # Handle door duration (for door_closed events)
                elif sensor_name == "door_duration":
                    door_events["door_open_duration"] = entry.get("v")
            
            return door_events if door_events else None
            
        except (json.JSONDecodeError, TypeError) as e:
            print(f"[SENML] Error parsing SenML payload: {e}")
            return None
    
    def notify(self, topic, payload):
        """Callback method for MyMQTT - handles incoming SenML door event messages"""
        try:
            # Handle configuration updates
            if "config_update" in topic:
                self.handle_config_update(topic, payload)
                return
            
            # Parse SenML payload
            door_event_data = self.parse_senml_payload(payload)
            if not door_event_data:
                print(f"[SENML] Failed to parse SenML door event from topic: {topic}")
                return
            
            topic_parts = topic.split('/')
            
            # Expected topic: Group17/SmartChill/Devices/{model}/{device_id}/door_event
            if len(topic_parts) >= 5 and topic_parts[-1] == "door_event":
                topic_device_id = topic_parts[-2]  # Extract device_id from topic
                device_id = door_event_data["device_id"] or topic_device_id
                event_type = door_event_data.get("event_type")
                
                # Check if we know this device - if not, verify with catalog
                if device_id not in self.known_devices:
                    print(f"[NEW_DEVICE] Unknown device detected: {device_id}")
                    if self.check_device_exists_in_catalog(device_id):
                        print(f"[NEW_DEVICE] Device {device_id} confirmed in catalog")
                    else:
                        print(f"[NEW_DEVICE] Device {device_id} not registered in catalog - ignoring event")
                        return
                
                if event_type == "door_opened":
                    self.handle_door_opened(device_id, door_event_data)
                elif event_type == "door_closed":
                    self.handle_door_closed(device_id, door_event_data)
                else:
                    print(f"[WARN] Unknown door event type: {event_type}")
                    
                print(f"[SENML] Processed door event: {device_id} - {event_type}")
            else:
                print(f"[WARN] Unexpected topic format: {topic}")
                
        except Exception as e:
            print(f"[ERROR] Error processing SenML door event: {e}")
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
    
    def handle_door_opened(self, device_id, event_data):
        """Handle door opened event - start timer"""
        current_time = time.time()
        self.device_timers[device_id] = current_time
        
        print(f"[TIMER] Door OPENED for {device_id} - timer started")
    
    def handle_door_closed(self, device_id, event_data):
        """Handle door closed event - stop timer and send alert if needed"""
        if device_id in self.device_timers:
            start_time = self.device_timers[device_id]
            duration = time.time() - start_time
            del self.device_timers[device_id]
            
            config = self.get_device_config(device_id)
            
            # Check if this device had an active alert and door closed alerts are enabled
            if (device_id in self.alerted_devices and 
                config.get("enable_door_closed_alerts", True)):
                self.send_door_closed_alert(device_id, duration)
                del self.alerted_devices[device_id]
                print(f"[TIMER] Door CLOSED for {device_id} after {duration:.1f}s - ALERT SENT")
            else:
                print(f"[TIMER] Door CLOSED for {device_id} after {duration:.1f}s - no alert needed")
        else:
            print(f"[TIMER] Door CLOSED for {device_id} but no active timer found")
    
    def check_door_timeouts(self):
        """Check for doors that have been open too long and send alerts"""
        current_time = time.time()
        
        for device_id, start_time in list(self.device_timers.items()):
            duration = current_time - start_time
            config = self.get_device_config(device_id)
            threshold = config["max_door_open_seconds"]
            
            # Check if threshold exceeded and alert not yet sent
            if duration >= threshold and device_id not in self.alerted_devices:
                self.send_door_timeout_alert(device_id, duration)
                self.alerted_devices[device_id] = current_time
                print(f"[TIMEOUT] Door timeout alert triggered for {device_id} - {duration:.0f}s > {threshold}s")
    
    def send_door_timeout_alert(self, device_id, duration):
        """Send door timeout alert via MQTT"""
        if not self.connected or not self.mqtt_client:
            print(f"[ALERT] Cannot send timeout alert - MQTT not connected")
            return
        
        config = self.get_device_config(device_id)
        threshold = config["max_door_open_seconds"]
        
        alert_topic = f"Group17/SmartChill/{device_id}/Alerts/DoorTimeout"
        alert_payload = {
            "alert_type": "door_timeout",
            "device_id": device_id,
            "message": f"Door has been open for {duration:.0f} seconds (threshold: {threshold}s)",
            "duration_seconds": round(duration, 1),
            "threshold_seconds": threshold,
            "severity": "warning",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": self.service_id,
            "config_version": self.settings["configVersion"]
        }
        
        try:
            self.mqtt_client.myPublish(alert_topic, alert_payload)
            print(f"[ALERT] Door timeout alert sent for {device_id}")
        except Exception as e:
            print(f"[ALERT] Error sending timeout alert: {e}")
    
    def send_door_closed_alert(self, device_id, total_duration):
        """Send door closed alert via MQTT"""
        if not self.connected or not self.mqtt_client:
            print(f"[ALERT] Cannot send door closed alert - MQTT not connected")
            return
        
        config = self.get_device_config(device_id)
        threshold = config["max_door_open_seconds"]
        
        alert_topic = f"Group17/SmartChill/{device_id}/Alerts/DoorClosed"
        alert_payload = {
            "alert_type": "door_closed_after_timeout",
            "device_id": device_id,
            "message": f"Door closed after {total_duration:.0f} seconds (was over {threshold}s threshold)",
            "total_duration_seconds": round(total_duration, 1),
            "threshold_seconds": threshold,
            "over_threshold_by": round(total_duration - threshold, 1),
            "severity": "info",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": self.service_id,
            "config_version": self.settings["configVersion"]
        }
        
        try:
            self.mqtt_client.myPublish(alert_topic, alert_payload)
            print(f"[ALERT] Door closed alert sent for {device_id}")
        except Exception as e:
            print(f"[ALERT] Error sending door closed alert: {e}")
    
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
    
    def monitoring_loop(self):
        """Main monitoring loop - checks for timeouts periodically"""
        while self.running:
            try:
                self.check_door_timeouts()
                
                # Show active timers status occasionally
                if self.device_timers:
                    current_time = time.time()
                    for device_id, start_time in self.device_timers.items():
                        duration = current_time - start_time
                        config = self.get_device_config(device_id)
                        threshold = config["max_door_open_seconds"]
                        
                        if device_id in self.alerted_devices:
                            print(f"[STATUS] {device_id}: {duration:.0f}s (ALERTED - waiting for close)")
                        else:
                            remaining = max(0, threshold - duration)
                            print(f"[STATUS] {device_id}: {duration:.0f}s (alert in {remaining:.0f}s)")
                
                # Use minimum check interval from active devices
                min_interval = min(
                    (self.get_device_config(device_id)["check_interval"] 
                     for device_id in self.device_timers.keys()),
                    default=self.settings["defaults"]["check_interval"]
                )
                
                time.sleep(min_interval)
                
            except Exception as e:
                print(f"[ERROR] Error in monitoring loop: {e}")
                time.sleep(5)
    
    def periodic_registration(self):
        """Periodically re-register with catalog"""
        interval = self.settings["catalog"]["registration_interval_seconds"]
        
        while self.running:
            time.sleep(interval)
            if self.running:
                print(f"[REGISTER] Periodic re-registration...")
                self.register_with_catalog()
    
    def run(self):
        """Main run method"""
        print("=" * 60)
        print("    SMARTCHILL TIMER USAGE CONTROL SERVICE (SenML)")
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
        print(f"[INIT] Processing SenML door events.")
        print(f"[INIT] Monitoring {len(self.settings['devices'])} configured devices")
        print(f"[INIT] Known devices from catalog: {len(self.known_devices)}")
        
        # Start background threads
        monitoring_thread = threading.Thread(target=self.monitoring_loop, daemon=True)
        registration_thread = threading.Thread(target=self.periodic_registration, daemon=True)
        
        monitoring_thread.start()
        registration_thread.start()
        
        # Main loop - keep service alive
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[SHUTDOWN] Received interrupt signal...")
            self.shutdown()
    
    def shutdown(self):
        """Graceful shutdown"""
        print("[SHUTDOWN] Stopping Timer Usage Control service...")
        self.running = False
        
        if self.mqtt_client:
            try:
                self.mqtt_client.stop()
                print("[SHUTDOWN] MQTT connection closed")
            except Exception as e:
                print(f"[SHUTDOWN] Error closing MQTT: {e}")
        
        print("[SHUTDOWN] Timer Usage Control service stopped")

def main():
    """Main entry point"""
    service = TimerUsageControl()
    
    try:
        service.run()
    except Exception as e:
        print(f"[FATAL] Service error: {e}")
    finally:
        service.shutdown()

if __name__ == "__main__":
    main()