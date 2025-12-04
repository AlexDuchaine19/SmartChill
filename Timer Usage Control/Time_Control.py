import time
import threading
import json
from datetime import datetime, timezone

from modules.utils import load_settings, save_settings
from modules.catalog_client import CatalogClient
from modules.mqtt_client import MQTTClient
from modules.timer_manager import TimerManager

class TimerUsageControl:
    def __init__(self, settings_file="settings.json"):
        self.settings_file = settings_file
        self.settings = load_settings(settings_file)
        
        # Service configuration
        self.service_info = self.settings["serviceInfo"]
        self.service_id = self.service_info["serviceID"]
        
        # Initialize modules
        self.catalog_client = CatalogClient(self.settings)
        self.mqtt_client = MQTTClient(self.settings, self)
        self.timer_manager = TimerManager(self)
        
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
                    "max_door_open_seconds": self.settings["defaults"]["max_door_open_seconds"],
                    "check_interval": self.settings["defaults"]["check_interval"],
                    "enable_door_closed_alerts": True
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
            door_event_data = self.parse_senml_payload(payload)
            if not door_event_data:
                print(f"[SENML] Failed to parse SenML door event from topic: {topic}")
                return
            
            topic_parts = topic.split('/')
            if len(topic_parts) >= 5 and topic_parts[-1] == "door_event":
                topic_device_id = topic_parts[-2]
                device_id = door_event_data["device_id"] or topic_device_id
                event_type = door_event_data.get("event_type")
                
                if device_id not in self.known_devices:
                    print(f"[NEW_DEVICE] Unknown device detected: {device_id}")
                    if self.catalog_client.check_device_exists(device_id):
                        self.known_devices.add(device_id)
                        self.auto_register_device(device_id)
                        print(f"[NEW_DEVICE] Device {device_id} confirmed in catalog")
                    else:
                        print(f"[NEW_DEVICE] Device {device_id} not registered in catalog - ignoring event")
                        return
                
                if event_type == "door_opened":
                    self.timer_manager.handle_door_opened(device_id)
                elif event_type == "door_closed":
                    self.timer_manager.handle_door_closed(device_id)
                else:
                    print(f"[WARN] Unknown door event type: {event_type}")
                    
                print(f"[SENML] Processed door event: {device_id} - {event_type}")
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
            
            door_events = {}
            for entry in entries:
                if not isinstance(entry, dict): continue
                sensor_name = entry.get("n")
                time_offset = entry.get("t", 0)
                timestamp = base_time + time_offset
                
                if sensor_name == "door_state":
                    door_events["event_type"] = entry.get("vs")
                    door_events["timestamp"] = timestamp
                    door_events["device_id"] = device_id
                elif sensor_name == "door_duration":
                    door_events["door_open_duration"] = entry.get("v")
            
            return door_events if door_events else None
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
                response_topic = f"Group17/SmartChill/TimerUsageControl/{device_id}/config_data"
                response_payload = {"device_id": device_id, "timestamp": datetime.now(timezone.utc).isoformat(), "config": {"max_door_open_seconds": current_config["max_door_open_seconds"], "check_interval": current_config["check_interval"], "enable_door_closed_alerts": current_config["enable_door_closed_alerts"]}}
                self.mqtt_client.publish(response_topic, response_payload)
                print(f"[CONFIG] Sent config_data for {device_id}")
            else:
                print(f"[CONFIG] Received config update for {device_id}: {config_data}")
                valid_keys = ["max_door_open_seconds", "check_interval", "enable_door_closed_alerts"]
                updates = {k: v for k, v in config_data.items() if k in valid_keys}
                if updates:
                    self.update_device_config(device_id, updates)
                    ack_topic = f"Group17/SmartChill/TimerUsageControl/{device_id}/config_ack"
                    ack_payload = {"device_id": device_id, "timestamp": datetime.now(timezone.utc).isoformat(), "updated_config": updates}
                    self.mqtt_client.publish(ack_topic, ack_payload)
                    print(f"[CONFIG] Config updated and acknowledged for {device_id}")
                else:
                    error_topic = f"Group17/SmartChill/TimerUsageControl/{device_id}/config_error"
                    error_payload = {"device_id": device_id, "timestamp": datetime.now(timezone.utc).isoformat(), "error": "No valid configuration keys provided"}
                    self.mqtt_client.publish(error_topic, error_payload)
                    print(f"[CONFIG] Invalid config update for {device_id}")
        except Exception as e:
            print(f"[CONFIG] Error handling config update: {e}")

    def monitoring_loop(self):
        """Main monitoring loop"""
        while self.running:
            try:
                self.timer_manager.check_timeouts()
                
                # Use minimum check interval from active devices
                min_interval = min(
                    (self.get_device_config(device_id)["check_interval"] 
                     for device_id in self.timer_manager.device_timers.keys()),
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
                self.catalog_client.register_service()

    def run(self):
        """Main run method"""
        print("=" * 60)
        print("    SMARTCHILL TIMER USAGE CONTROL SERVICE (MODULAR)")
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
        
        monitoring_thread = threading.Thread(target=self.monitoring_loop, daemon=True)
        registration_thread = threading.Thread(target=self.periodic_registration, daemon=True)
        
        monitoring_thread.start()
        registration_thread.start()
        
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
        self.mqtt_client.stop()
        print("[SHUTDOWN] Timer Usage Control service stopped")

if __name__ == "__main__":
    service = TimerUsageControl()
    try:
        service.run()
    except Exception as e:
        print(f"[FATAL] Service error: {e}")
    finally:
        service.shutdown()