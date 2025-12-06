import json
import time
import threading
import requests
from datetime import datetime, timezone
from MyMQTT import MyMQTT

from timer_utils import (
    parse_senml_door_event,
    validate_config_values,
    check_timeout_condition,
    calculate_duration
)

class TimerUsageControl:
    def __init__(self, settings_file="settings.json"):
        self.settings_file = settings_file
        self.settings = self.load_settings()
        
        # Service configuration
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
        self.known_devices = set()   # Cache
        
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
        for endpoint in self.service_info["endpoints"]:
            if endpoint.startswith("MQTT Subscribe: "):
                topic = endpoint.replace("MQTT Subscribe: ", "")
                subscribe_topics.append(topic)
        return subscribe_topics
    
    def register_with_catalog(self, max_retries=5):
        """Register service with catalog"""
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
                
                response = requests.post(f"{self.catalog_url}/services/register", json=registration_data, timeout=5)
                if response.status_code in [200, 201]:
                    print(f"[REGISTER] Successfully registered with catalog")
                    return True
            except requests.RequestException as e:
                print(f"[REGISTER] Error: {e}")
            
            if attempt < max_retries - 1: time.sleep(2)
        return False
    
    def check_device_exists_in_catalog(self, device_id):
        """Check if device exists in catalog via REST API"""
        try:
            response = requests.get(f"{self.catalog_url}/devices/{device_id}/exists", timeout=5)
            if response.status_code == 200:
                result = response.json()
                if result.get("exists", False):
                    self.known_devices.add(device_id)
                    if device_id not in self.settings["devices"]:
                        self.auto_register_device(device_id)
                    return True
                else:
                    print(f"[DEVICE_CHECK] Device {device_id} not found")
                    return False
            return False
        except requests.RequestException as e:
            print(f"[DEVICE_CHECK] Error: {e}")
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
                print(f"[AUTO-REG] Device {device_id} registered with defaults")
    
    def get_device_config(self, device_id):
        """Get configuration for specific device"""
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
            print(f"[CONFIG] Updated config for {device_id}")

    # ===================== Config Handling (MQTT) =====================

    def handle_config_update(self, topic, payload):
        """Handle configuration update/get via MQTT"""
        try:
            message = json.loads(payload)
            topic_parts = topic.split('/')
            if len(topic_parts) < 5: return
            
            requester = topic_parts[3]
            msg_type = message.get("type")
            
            # GET Config
            if msg_type == "config_get":
                device_id = message.get("device_id")
                if device_id:
                    if requester != "admin" and requester != device_id:
                        self.send_config_error("access_denied", "Access denied", topic, device_id); return
                    
                    if requester != "admin" and device_id not in self.known_devices:
                        if not self.check_device_exists_in_catalog(device_id):
                            self.send_config_error("device_not_found", "Not found", topic, device_id); return
                    
                    self.send_config_data(device_id, "device_config", self.get_device_config(device_id), topic)
                else:
                    if requester != "admin": self.send_config_error("access_denied", "Admin only", topic); return
                    self.send_config_data(None, "default_config", self.settings["defaults"], topic)
            
            # UPDATE Device Config
            elif msg_type == "device_config_update":
                device_id = message.get("device_id")
                new_config = message.get("config", {})
                
                if not device_id or not new_config:
                    self.send_config_error("missing_fields", "Missing data", topic, device_id); return
                
                if requester != "admin" and requester != device_id:
                    self.send_config_error("access_denied", "Access denied", topic, device_id); return

                val_err = validate_config_values(new_config)
                if val_err: self.send_config_error("invalid_config", val_err, topic, device_id); return
                
                self.update_device_config(device_id, new_config)
                self.send_config_ack(device_id, "device_updated", new_config, topic)
            
            # UPDATE Default Config
            elif msg_type == "default_config_update":
                new_config = message.get("config", {})
                if requester != "admin": self.send_config_error("access_denied", "Admin only", topic); return
                
                val_err = validate_config_values(new_config)
                if val_err: self.send_config_error("invalid_config", val_err, topic); return
                
                with self.config_lock:
                    self.settings["defaults"].update(new_config)
                    self.save_settings()
                self.send_config_ack(None, "defaults_updated", new_config, topic)
                
        except Exception as e:
            self.send_config_error("internal_error", str(e), topic)
            print(f"[CONFIG] Error: {e}")

    # ===================== Config Response Helpers =====================

    def send_config_data(self, device_id, data_type, config, original_topic):
        self._send_mqtt_response(original_topic, "config_data", {"device_id": device_id, "data_type": data_type, "config": config})

    def send_config_ack(self, device_id, status, config, original_topic):
        self._send_mqtt_response(original_topic, "config_ack", {"device_id": device_id, "status": status, "config": config})

    def send_config_error(self, error_code, error_message, original_topic, device_id=None):
        self._send_mqtt_response(original_topic, "config_error", {"device_id": device_id, "error_code": error_code, "error_message": error_message})

    def _send_mqtt_response(self, original_topic, suffix, payload_extra):
        if not self.connected or not self.mqtt_client: return
        try:
            topic_parts = original_topic.split('/')
            requester = topic_parts[3] if len(topic_parts) > 4 else "unknown"
            topic = f"Group17/SmartChill/TimerUsageControl/{requester}/{suffix}"
            payload = {"timestamp": datetime.now(timezone.utc).isoformat(), "config_version": self.settings["configVersion"], "original_topic": original_topic, **payload_extra}
            self.mqtt_client.myPublish(topic, payload)
        except Exception as e: print(f"[CONFIG] Error sending response: {e}")

    # ===================== Door Event Logic =====================

    def handle_door_opened(self, device_id, event_data):
        """Handle door opened event - start timer"""
        self.device_timers[device_id] = time.time()
        print(f"[TIMER] Door OPENED for {device_id}")
    
    def handle_door_closed(self, device_id, event_data):
        """Handle door closed event - stop timer and alert if needed"""
        if device_id in self.device_timers:
            duration = calculate_duration(self.device_timers[device_id])
            del self.device_timers[device_id]
            
            config = self.get_device_config(device_id)
            
            # If device was alerted, send closed notification
            if device_id in self.alerted_devices and config.get("enable_door_closed_alerts", True):
                self.send_door_closed_alert(device_id, duration)
                del self.alerted_devices[device_id]
                print(f"[TIMER] Door CLOSED for {device_id} after {duration:.1f}s - ALERT SENT")
            else:
                print(f"[TIMER] Door CLOSED for {device_id} after {duration:.1f}s")
    
    def check_door_timeouts(self):
        """Check for open doors exceeding thresholds"""
        current_time = time.time()
        
        for device_id, start_time in list(self.device_timers.items()):
            duration = calculate_duration(start_time, current_time)
            config = self.get_device_config(device_id)
            threshold = config["max_door_open_seconds"]
            
            if check_timeout_condition(duration, threshold) and device_id not in self.alerted_devices:
                self.send_door_timeout_alert(device_id, duration)
                self.alerted_devices[device_id] = current_time
                print(f"[TIMEOUT] Alert for {device_id} - {duration:.0f}s > {threshold}s")
    
    def send_door_timeout_alert(self, device_id, duration):
        if not self.connected: return
        config = self.get_device_config(device_id)
        
        alert_topic = f"Group17/SmartChill/{device_id}/Alerts/DoorTimeout"
        alert_payload = {
            "alert_type": "door_timeout",
            "device_id": device_id,
            "message": f"Door open for {duration:.0f}s (threshold: {config['max_door_open_seconds']}s)",
            "duration_seconds": round(duration, 1),
            "threshold_seconds": config['max_door_open_seconds'],
            "severity": "warning",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": self.service_id
        }
        self.mqtt_client.myPublish(alert_topic, alert_payload)
    
    def send_door_closed_alert(self, device_id, total_duration):
        if not self.connected: return
        config = self.get_device_config(device_id)
        
        alert_topic = f"Group17/SmartChill/{device_id}/Alerts/DoorClosed"
        alert_payload = {
            "alert_type": "door_closed_after_timeout",
            "device_id": device_id,
            "message": f"Door closed after {total_duration:.0f}s (was over threshold)",
            "total_duration_seconds": round(total_duration, 1),
            "threshold_seconds": config['max_door_open_seconds'],
            "severity": "info",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": self.service_id
        }
        self.mqtt_client.myPublish(alert_topic, alert_payload)

    # ===================== MQTT & Lifecycle =====================

    def notify(self, topic, payload):
        """Callback for SenML door events"""
        try:
            if "config_update" in topic:
                self.handle_config_update(topic, payload); return
            
            door_event_data = parse_senml_door_event(payload)
            if not door_event_data: return
            
            topic_parts = topic.split('/')
            device_id = door_event_data.get("device_id") or (topic_parts[-2] if len(topic_parts)>=5 else None)
            event_type = door_event_data.get("event_type")
            
            if device_id not in self.known_devices:
                if not self.check_device_exists_in_catalog(device_id): return
            
            if event_type == "door_opened":
                self.handle_door_opened(device_id, door_event_data)
            elif event_type == "door_closed":
                self.handle_door_closed(device_id, door_event_data)
                
        except Exception as e:
            print(f"[ERROR] notify: {e}")
            import traceback; traceback.print_exc()

    def setup_mqtt(self):
        try:
            client_id = f"{self.settings['mqtt']['clientID_prefix']}_{int(time.time())}"
            self.mqtt_client = MyMQTT(client_id, self.broker_host, self.broker_port, self)
            self.mqtt_client.start()
            time.sleep(2)
            self.connected = True
            
            topics = self.extract_mqtt_topics()
            for topic in topics:
                self.mqtt_client.mySubscribe(topic)
                print(f"[MQTT] Subscribed: {topic}")
            return True
        except Exception as e: print(f"[MQTT] Error: {e}"); return False

    def load_known_devices_from_catalog(self):
        try:
            response = requests.get(f"{self.catalog_url}/devices", timeout=5)
            if response.status_code == 200:
                for device in response.json():
                    did = device.get("deviceID")
                    if did and did.startswith("SmartChill_"):
                        self.known_devices.add(did)
                        if did not in self.settings["devices"]: self.auto_register_device(did)
                print(f"[INIT] Loaded {len(self.known_devices)} devices")
        except Exception as e: print(f"[INIT] Catalog error: {e}")

    def monitoring_loop(self):
        while self.running:
            try:
                self.check_door_timeouts()
                time.sleep(self.settings["defaults"]["check_interval"])
            except Exception as e: print(f"[ERROR] Loop: {e}"); time.sleep(5)

    def periodic_registration(self):
        interval = self.settings["catalog"]["registration_interval_seconds"]
        while self.running:
            time.sleep(interval)
            if self.running: self.register_with_catalog()

    def run(self):
        print("="*60 + "\n    SMARTCHILL TIMER USAGE CONTROL\n" + "="*60)
        self.register_with_catalog()
        self.load_known_devices_from_catalog()
        if not self.setup_mqtt(): return
        
        threading.Thread(target=self.monitoring_loop, daemon=True).start()
        threading.Thread(target=self.periodic_registration, daemon=True).start()
        
        try:
            while self.running: time.sleep(1)
        except KeyboardInterrupt: self.shutdown()

    def shutdown(self):
        print("[SHUTDOWN] Stopping service..."); self.running = False
        if self.mqtt_client: self.mqtt_client.stop()