import time
import threading
import json
from datetime import datetime, timezone

from modules.utils import load_settings, save_settings
from modules.catalog_client import CatalogClient
from modules.mqtt_client import MQTTClient
from modules.spoilage_monitor import SpoilageMonitor

class FoodSpoilageControl:
    def __init__(self, settings_file="settings.json"):
        self.settings_file = settings_file
        self.settings = load_settings(settings_file)
        
        # Service configuration
        self.service_info = self.settings["serviceInfo"]
        self.service_id = self.service_info["serviceID"]
        
        # Initialize modules
        self.catalog_client = CatalogClient(self.settings)
        self.mqtt_client = MQTTClient(self.settings, self)
        self.spoilage_monitor = SpoilageMonitor(self)
        
        # Device management
        self.known_devices = set()
        self.config_lock = threading.RLock()
        
        # Threading
        self.running = True
        
        print(f"[INIT] {self.service_id} service starting...")

    def save_settings(self):
        """Save current settings to file"""
        save_settings(self.settings, self.settings_file, self.config_lock)

    def get_device_config(self, device_id):
        """Get configuration for specific device, with fallback to default"""
        with self.config_lock:
            device_config = self.settings["devices"].get(device_id, {})
            defaults = self.settings["defaults"]
            return {**defaults, **device_config}

    def update_device_config(self, device_id, new_config):
        """Update configuration for a specific device"""
        with self.config_lock:
            if device_id not in self.settings["devices"]:
                self.settings["devices"][device_id] = {}
            self.settings["devices"][device_id].update(new_config)
            self.save_settings()
            print(f"[CONFIG] Updated configuration for {device_id}: {new_config}")

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

    def handle_message(self, topic, payload):
        """Handle incoming MQTT messages"""
        try:
            if "config_update" in topic:
                self.handle_config_update(topic, payload)
                return
            
            # Parse SenML payload
            parsed_data = self.parse_senml_payload(payload)
            if not parsed_data:
                print(f"[SENML] Failed to parse SenML data from topic: {topic}")
                return
            
            topic_parts = topic.split('/')
            if len(topic_parts) >= 5 and topic_parts[-1] == "gas":
                topic_device_id = topic_parts[-2]
                
                for data_entry in parsed_data:
                    device_id = data_entry["device_id"] or topic_device_id
                    sensor_name = data_entry["sensor_name"]
                    value = data_entry["value"]
                    timestamp = data_entry["timestamp"]
                    
                    if sensor_name != "gas": continue
                    
                    if device_id not in self.known_devices:
                        print(f"[NEW_DEVICE] Unknown device detected: {device_id}")
                        if self.catalog_client.check_device_exists(device_id):
                            self.known_devices.add(device_id)
                            self.auto_register_device(device_id)
                            print(f"[NEW_DEVICE] Device {device_id} confirmed in catalog")
                        else:
                            print(f"[NEW_DEVICE] Device {device_id} not registered in catalog - ignoring data")
                            continue
                    
                    # Convert timestamp
                    if timestamp:
                        try:
                            ts = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                        except (ValueError, TypeError):
                            ts = datetime.now(timezone.utc)
                    else:
                        ts = datetime.now(timezone.utc)
                    
                    self.spoilage_monitor.handle_gas_reading(device_id, float(value), ts)
                    print(f"[SENML] Processed gas data: {device_id} = {value} PPM")
            else:
                print(f"[WARN] Unexpected topic format: {topic}")
                
        except Exception as e:
            print(f"[ERROR] Error processing message: {e}")

    def parse_senml_payload(self, payload):
        """Parse SenML formatted payload"""
        try:
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8")
            senml_data = json.loads(payload) if isinstance(payload, str) else payload
            
            if not isinstance(senml_data, dict) or "e" not in senml_data:
                return None
            
            base_name = senml_data.get("bn", "")
            base_time = senml_data.get("bt", 0)
            entries = senml_data.get("e", [])
            device_id = base_name.rstrip("/") if base_name.endswith("/") else None
            
            parsed_data = []
            for entry in entries:
                if not isinstance(entry, dict): continue
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
        except Exception as e:
            print(f"[SENML] Error parsing SenML payload: {e}")
            return None

    def handle_config_update(self, topic, payload):
        """Handle configuration update/get via MQTT"""
        try:
            if isinstance(payload, bytes):
                payload = payload.decode('utf-8')
            data = json.loads(payload) if isinstance(payload, str) else payload
            topic_parts = topic.split('/')
            if len(topic_parts) >= 5:
                device_id = topic_parts[3]
            else:
                print(f"[CONFIG] Invalid topic format: {topic}")
                return
            config_data = data.get('config', {})
            if config_data.get('request') == 'get_config':
                print(f"[CONFIG] Received get_config request for {device_id}")
                current_config = self.get_device_config(device_id)
                response_topic = f"Group17/SmartChill/FoodSpoilageControl/{device_id}/config_data"
                response_payload = {"device_id": device_id, "timestamp": datetime.now(timezone.utc).isoformat(), "config": {"gas_threshold_ppm": current_config["gas_threshold_ppm"], "enable_continuous_alerts": current_config["enable_continuous_alerts"], "alert_cooldown_minutes": current_config["alert_cooldown_minutes"]}}
                self.mqtt_client.publish(response_topic, response_payload)
                print(f"[CONFIG] Sent config_data for {device_id}")
            else:
                print(f"[CONFIG] Received config update for {device_id}: {config_data}")
                valid_keys = ["gas_threshold_ppm", "enable_continuous_alerts", "alert_cooldown_minutes"]
                updates = {k: v for k, v in config_data.items() if k in valid_keys}
                if updates:
                    self.update_device_config(device_id, updates)
                    ack_topic = f"Group17/SmartChill/FoodSpoilageControl/{device_id}/config_ack"
                    ack_payload = {"device_id": device_id, "timestamp": datetime.now(timezone.utc).isoformat(), "updated_config": updates}
                    self.mqtt_client.publish(ack_topic, ack_payload)
                    print(f"[CONFIG] Config updated and acknowledged for {device_id}")
                else:
                    error_topic = f"Group17/SmartChill/FoodSpoilageControl/{device_id}/config_error"
                    error_payload = {"device_id": device_id, "timestamp": datetime.now(timezone.utc).isoformat(), "error": "No valid configuration keys provided"}
                    self.mqtt_client.publish(error_topic, error_payload)
                    print(f"[CONFIG] Invalid config update for {device_id}")
        except Exception as e:
            print(f"[CONFIG] Error handling config update: {e}")

    def periodic_registration(self):
        """Periodically re-register with catalog"""
        interval = self.settings["catalog"]["registration_interval_seconds"]
        while self.running:
            time.sleep(interval)
            if self.running:
                print(f"[REGISTER] Periodic re-registration...")
                self.catalog_client.register_service()

    def status_monitor_loop(self):
        """Monitor service status"""
        while self.running:
            try:
                if self.spoilage_monitor.gas_status:
                    print(f"[STATUS] Current gas status:")
                    for device_id, status in self.spoilage_monitor.gas_status.items():
                        config = self.get_device_config(device_id)
                        threshold = config["gas_threshold_ppm"]
                        cooldown_active = self.spoilage_monitor.is_cooldown_active(device_id)
                        cooldown_str = " (COOLDOWN)" if cooldown_active else ""
                        print(f"  {device_id}: {status} (threshold: {threshold} PPM){cooldown_str}")
                time.sleep(self.settings["catalog"]["ping_interval_seconds"])
            except Exception as e:
                print(f"[STATUS] Error in status monitor: {e}")
                time.sleep(30)

    def run(self):
        """Main run method"""
        print("=" * 60)
        print("    SMARTCHILL FOOD SPOILAGE CONTROL SERVICE (MODULAR)")
        print("=" * 60)
        
        print("[INIT] Registering service with catalog...")
        if not self.catalog_client.register_service():
            print("[WARN] Failed to register with catalog - continuing anyway")
        
        print("[INIT] Loading known devices from catalog...")
        self.known_devices = self.catalog_client.load_known_devices()
        for device_id in self.known_devices:
            self.auto_register_device(device_id)
        
        print("[INIT] Setting up MQTT connection...")
        if not self.mqtt_client.start():
            print("[ERROR] Failed to setup MQTT connection")
            return
        
        print(f"[INIT] Service started successfully!")
        
        registration_thread = threading.Thread(target=self.periodic_registration, daemon=True)
        status_thread = threading.Thread(target=self.status_monitor_loop, daemon=True)
        
        registration_thread.start()
        status_thread.start()
        
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
        self.mqtt_client.stop()
        print("[SHUTDOWN] Food Spoilage Control service stopped")

if __name__ == "__main__":
    service = FoodSpoilageControl()
    try:
        service.run()
    except Exception as e:
        print(f"[FATAL] Service error: {e}")
    finally:
        service.shutdown()