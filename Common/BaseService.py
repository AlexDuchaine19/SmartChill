import json
import time
import threading
import requests
import random
from datetime import datetime, timezone
from MyMQTT import MyMQTT

class SmartChillBaseService:
    def __init__(self, settings_file="settings.json", service_type_name="Generic"):
        self.settings_file = settings_file
        self.settings = self.load_settings()
        
        # Service configuration
        self.service_info = self.settings["serviceInfo"]
        self.service_id = self.service_info["serviceID"]
        self.catalog_url = self.settings["catalog"]["url"]
        self.service_type_name = service_type_name
        
        # MQTT
        self.mqtt_client = None
        self.broker_host = self.settings["mqtt"]["brokerIP"]
        self.broker_port = self.settings["mqtt"]["brokerPort"]
        self.connected = False
        
        # Device management
        self.known_devices = set()
        
        # Threading
        self.running = True
        self.config_lock = threading.RLock()
        
        print(f"[INIT] {self.service_id} ({self.service_type_name}) starting...")

    def load_settings(self):
        try:
            with open(self.settings_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"[ERROR] Failed to load settings: {e}")
            raise

    def save_settings(self):
        with self.config_lock:
            self.settings["lastUpdate"] = datetime.now(timezone.utc).isoformat()
            self.settings["configVersion"] += 1
            try:
                with open(self.settings_file, 'w') as f:
                    json.dump(self.settings, f, indent=4)
                print(f"[CONFIG] Settings saved.")
            except Exception as e:
                print(f"[ERROR] Failed to save settings: {e}")

    def register_with_catalog(self, max_retries=3):
        """Standard registration logic"""
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
                res = requests.post(f"{self.catalog_url}/services/register", json=registration_data, timeout=5)
                if res.status_code in [200, 201]:
                    print(f"[REGISTER] Registered with catalog")
                    return True
            except Exception as e:
                print(f"[REGISTER] Error (attempt {attempt+1}): {e}")
            time.sleep(2)
        return False

    def check_device_exists_in_catalog(self, device_id):
        try:
            res = requests.get(f"{self.catalog_url}/devices/{device_id}/exists", timeout=5)
            if res.status_code == 200 and res.json().get("exists"):
                self.known_devices.add(device_id)
                if device_id not in self.settings["devices"]:
                    self.auto_register_device(device_id)
                return True
        except Exception as e:
            print(f"[CHECK] Catalog error: {e}")
        return False

    def load_known_devices_from_catalog(self):
        try:
            res = requests.get(f"{self.catalog_url}/devices", timeout=5)
            if res.status_code == 200:
                for dev in res.json():
                    did = dev.get("deviceID")
                    if did and did.startswith("SmartChill_"):
                        self.known_devices.add(did)
                        if did not in self.settings["devices"]:
                            self.auto_register_device(did)
                print(f"[INIT] Loaded {len(self.known_devices)} devices from catalog")
        except Exception as e:
            print(f"[INIT] Error loading devices: {e}")

    def auto_register_device(self, device_id):
        with self.config_lock:
            if device_id not in self.settings["devices"]:
                # Initialize with specific default structure from child class
                self.settings["devices"][device_id] = self.get_default_device_config()
                self.save_settings()
                print(f"[AUTO-REG] Device {device_id} registered locally")

    def get_default_device_config(self):
        """Override this in child class to return specific default dict"""
        return {}

    def get_device_config(self, device_id):
        with self.config_lock:
            dev_conf = self.settings["devices"].get(device_id, {})
            defaults = self.settings["defaults"]
            return {**defaults, **dev_conf}

    def update_device_config(self, device_id, new_config):
        with self.config_lock:
            if device_id not in self.settings["devices"]:
                self.settings["devices"][device_id] = {}
            self.settings["devices"][device_id].update(new_config)
            self.save_settings()

    # --- MQTT & Notification Handling ---

    def setup_mqtt(self):
        try:
            client_id = f"{self.settings['mqtt']['clientID_prefix']}_{int(time.time())}"
            self.mqtt_client = MyMQTT(client_id, self.broker_host, self.broker_port, self)
            self.mqtt_client.start()
            time.sleep(1)
            self.connected = True
            
            subs, _ = self.extract_mqtt_topics()
            for topic in subs:
                self.mqtt_client.mySubscribe(topic)
                print(f"[MQTT] Subscribed: {topic}")
            return True
        except Exception as e:
            print(f"[MQTT] Error: {e}")
            return False

    def extract_mqtt_topics(self):
        subs, pubs = [], []
        for ep in self.service_info["endpoints"]:
            if ep.startswith("MQTT Subscribe: "): subs.append(ep.replace("MQTT Subscribe: ", ""))
            elif ep.startswith("MQTT Publish: "): pubs.append(ep.replace("MQTT Publish: ", ""))
        return subs, pubs

    def notify(self, topic, payload):
        """Main router for MQTT messages"""
        try:
            if "config_update" in topic:
                self.handle_config_update(topic, payload)
            else:
                # Delegate sensor data to child class
                self.process_sensor_data(topic, payload)
        except Exception as e:
            print(f"[ERROR] Notify error: {e}")
            import traceback
            traceback.print_exc()

    def process_sensor_data(self, topic, payload):
        """Override in child class to handle SenML"""
        pass

    # --- Configuration Protocol (Identical in both) ---

    def handle_config_update(self, topic, payload):
        try:
            msg = json.loads(payload)
            parts = topic.split('/')
            requester = parts[3] if len(parts) >= 4 else "unknown"
            
            msg_type = msg.get("type")
            
            if msg_type == "config_get":
                self._handle_config_get(requester, msg, topic)
            elif msg_type == "device_config_update":
                self._handle_device_update(requester, msg, topic)
            elif msg_type == "default_config_update":
                self._handle_default_update(requester, msg, topic)
            else:
                self.send_config_error("unknown_type", f"Unknown type: {msg_type}", topic)
                
        except json.JSONDecodeError:
            self.send_config_error("invalid_json", "Invalid JSON", topic)

    def _handle_config_get(self, requester, msg, topic):
        device_id = msg.get("device_id")
        if device_id:
            # Check access
            if requester != "admin" and requester != device_id:
                 self.send_config_error("access_denied", "Denied", topic, device_id); return
            
            # Check existence
            if requester != "admin" and device_id not in self.known_devices:
                 if not self.check_device_exists_in_catalog(device_id):
                     self.send_config_error("device_not_found", "Not found", topic, device_id); return
            
            self.send_config_data(device_id, "device_config", self.get_device_config(device_id), topic)
        else:
            if requester != "admin":
                self.send_config_error("access_denied", "Admin only", topic); return
            self.send_config_data(None, "default_config", self.settings["defaults"], topic)

    def _handle_device_update(self, requester, msg, topic):
        device_id = msg.get("device_id")
        new_config = msg.get("config", {})
        
        if not device_id or not new_config:
            self.send_config_error("missing_fields", "Missing data", topic, device_id); return

        if requester != "admin" and requester != device_id:
             self.send_config_error("access_denied", "Denied", topic, device_id); return

        err = self.validate_specific_config(new_config) # Child hook
        if err:
             self.send_config_error("invalid_config", err, topic, device_id); return

        self.update_device_config(device_id, new_config)
        self.send_config_ack(device_id, "device_updated", new_config, topic)

    def _handle_default_update(self, requester, msg, topic):
        new_config = msg.get("config", {})
        if requester != "admin":
             self.send_config_error("access_denied", "Admin only", topic); return
        
        err = self.validate_specific_config(new_config) # Child hook
        if err:
             self.send_config_error("invalid_config", err, topic); return

        with self.config_lock:
            self.settings["defaults"].update(new_config)
            self.save_settings()
        self.send_config_ack(None, "defaults_updated", new_config, topic)

    def validate_specific_config(self, config):
        """Override in child class. Return error string or None"""
        return None

    def send_config_data(self, device_id, type_, config, orig_topic):
        self._send_mqtt_response("config_data", device_id, type_, config, orig_topic)

    def send_config_ack(self, device_id, status, config, orig_topic):
        payload = {"status": status, "config": config}
        self._send_mqtt_response("config_ack", device_id, None, payload, orig_topic)

    def send_config_error(self, code, msg, orig_topic, device_id=None):
        payload = {"error_code": code, "error_message": msg}
        self._send_mqtt_response("config_error", device_id, None, payload, orig_topic)

    def _send_mqtt_response(self, suffix, device_id, type_, data, orig_topic):
        if not self.connected: return
        parts = orig_topic.split('/')
        requester = parts[3] if len(parts) > 4 else "unknown"
        # Topic format: Group17/SmartChill/{Service}/{requester}/{suffix}
        topic = f"Group17/SmartChill/{self.service_type_name}/{requester}/{suffix}"
        
        final_payload = {
            "device_id": device_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "original_topic": orig_topic,
            **({"data_type": type_} if type_ else {}),
            **(data if isinstance(data, dict) else {"data": data})
        }
        self.mqtt_client.myPublish(topic, final_payload)

    def parse_senml(self, payload):
        """Helper to parse SenML"""
        try:
            if isinstance(payload, bytes): payload = payload.decode("utf-8")
            data = json.loads(payload) if isinstance(payload, str) else payload
            if "e" not in data: return None
            
            bn = data.get("bn", "")
            base_dev_id = bn.rstrip("/") if bn.endswith("/") else None
            bt = data.get("bt", 0)
            
            result = []
            for e in data["e"]:
                result.append({
                    "device_id": base_dev_id,
                    "n": e.get("n"),
                    "v": e.get("v"),
                    "vs": e.get("vs"),
                    "t": bt + e.get("t", 0)
                })
            return result
        except:
            return None

    def periodic_registration_loop(self):
        interval = self.settings["catalog"]["registration_interval_seconds"]
        while self.running:
            time.sleep(interval)
            if self.running: self.register_with_catalog()

    def shutdown(self):
        self.running = False
        if self.mqtt_client: self.mqtt_client.stop()
        print(f"[SHUTDOWN] {self.service_id} stopped")

    def run(self):
        """Standard startup sequence"""
        self.register_with_catalog()
        self.load_known_devices_from_catalog()
        self.setup_mqtt()
        
        reg_thread = threading.Thread(target=self.periodic_registration_loop, daemon=True)
        reg_thread.start()
        
        # Hook for child specific startup
        self.start_specific_tasks()
        
        try:
            while self.running: time.sleep(1)
        except KeyboardInterrupt:
            self.shutdown()

    def start_specific_tasks(self):
        """Override to start custom threads"""
        pass