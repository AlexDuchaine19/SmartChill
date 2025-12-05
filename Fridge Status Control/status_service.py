import json
import time
import threading
import requests
from datetime import datetime, timezone
from MyMQTT import MyMQTT

# Importiamo le utility dal file creato precedentemente
from status_utils import (
    parse_senml_payload,
    validate_config_values,
    evaluate_temperature,
    evaluate_humidity,
    evaluate_complex_patterns,
    format_timestamp
)

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
        self.last_alert_time = {}           # {device_id: {alert_type: timestamp}}
        self.device_status = {}             # {device_id: {"temp_status": ..., "humidity_status": ...}}
        self.known_devices = set()          # Cache of devices
        self.last_readings = {}             # {device_id: {"temperature": val, "humidity": val, "timestamp": ts}}
        
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
    
    def extract_mqtt_topics(self):
        """Extract MQTT topics from service endpoints"""
        subscribe_topics = []
        for endpoint in self.service_info["endpoints"]:
            if endpoint.startswith("MQTT Subscribe: "):
                topic = endpoint.replace("MQTT Subscribe: ", "")
                subscribe_topics.append(topic)
        return subscribe_topics
    
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
                if result.get("exists", False):
                    self.known_devices.add(device_id)
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
            return {**defaults, **device_config}
    
    def update_device_config(self, device_id, new_config):
        """Update configuration for a specific device"""
        with self.config_lock:
            if device_id not in self.settings["devices"]:
                self.settings["devices"][device_id] = {}
            
            self.settings["devices"][device_id].update(new_config)
            self.save_settings()
            print(f"[CONFIG] Updated configuration for {device_id}: {new_config}")

    # ===================== Config Handling (MQTT) =====================
    
    def handle_config_update(self, topic, payload):
        """Handle configuration update/get via MQTT"""
        try:
            message = json.loads(payload)
            topic_parts = topic.split('/')
            if len(topic_parts) < 5:
                self.send_config_error("invalid_topic", "Invalid topic format", topic)
                return
            
            requester = topic_parts[3]
            msg_type = message.get("type")
            
            # GET Config
            if msg_type == "config_get":
                device_id = message.get("device_id")
                if device_id:
                    if requester != "admin" and requester != device_id:
                        self.send_config_error("access_denied", f"Access denied for {device_id}", topic, device_id)
                        return
                    if requester != "admin" and device_id not in self.known_devices:
                        if not self.check_device_exists_in_catalog(device_id):
                            self.send_config_error("device_not_found", "Device not found", topic, device_id)
                            return
                    
                    device_config = self.get_device_config(device_id)
                    self.send_config_data(device_id, "device_config", device_config, topic)
                else:
                    if requester != "admin":
                        self.send_config_error("access_denied", "Admin only", topic)
                        return
                    self.send_config_data(None, "default_config", self.settings["defaults"], topic)
            
            # UPDATE Device Config
            elif msg_type == "device_config_update":
                device_id = message.get("device_id")
                new_config = message.get("config", {})
                
                if not device_id or not new_config:
                    self.send_config_error("missing_fields", "Missing data", topic, device_id)
                    return
                
                if requester != "admin" and requester != device_id:
                    self.send_config_error("access_denied", "Access denied", topic, device_id)
                    return

                # Validate using util function
                validation_error = validate_config_values(new_config)
                if validation_error:
                    self.send_config_error("invalid_config", validation_error, topic, device_id)
                    return
                
                self.update_device_config(device_id, new_config)
                self.send_config_ack(device_id, "device_updated", new_config, topic)
            
            # UPDATE Default Config
            elif msg_type == "default_config_update":
                new_config = message.get("config", {})
                if requester != "admin":
                    self.send_config_error("access_denied", "Admin only", topic)
                    return
                
                validation_error = validate_config_values(new_config)
                if validation_error:
                    self.send_config_error("invalid_config", validation_error, topic)
                    return
                
                with self.config_lock:
                    self.settings["defaults"].update(new_config)
                    self.save_settings()
                self.send_config_ack(None, "defaults_updated", new_config, topic)
            
            else:
                self.send_config_error("unknown_type", f"Unknown type: {msg_type}", topic)
                
        except json.JSONDecodeError as e:
            self.send_config_error("invalid_json", str(e), topic)
        except Exception as e:
            self.send_config_error("internal_error", str(e), topic)
            print(f"[CONFIG] Error: {e}")

    # ===================== Config Response Helpers =====================

    def send_config_data(self, device_id, data_type, config, original_topic):
        self._send_mqtt_response(original_topic, "config_data", {
            "device_id": device_id, "data_type": data_type, "config": config
        })

    def send_config_ack(self, device_id, status, config, original_topic):
        self._send_mqtt_response(original_topic, "config_ack", {
            "device_id": device_id, "status": status, "config": config
        })

    def send_config_error(self, error_code, error_message, original_topic, device_id=None):
        self._send_mqtt_response(original_topic, "config_error", {
            "device_id": device_id, "error_code": error_code, "error_message": error_message
        })

    def _send_mqtt_response(self, original_topic, suffix, payload_extra):
        if not self.connected or not self.mqtt_client: return
        try:
            topic_parts = original_topic.split('/')
            requester = topic_parts[3] if len(topic_parts) > 4 else "unknown"
            resp_topic = f"Group17/SmartChill/FridgeStatusControl/{requester}/{suffix}"
            
            payload = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "config_version": self.settings["configVersion"],
                "original_topic": original_topic,
                **payload_extra
            }
            self.mqtt_client.myPublish(resp_topic, payload)
        except Exception as e: print(f"[CONFIG] Error sending response: {e}")

    # ===================== Analysis Logic =====================
    
    def is_cooldown_active(self, device_id, alert_type):
        """Check if device is in alert cooldown period for specific alert type"""
        if device_id not in self.last_alert_time or alert_type not in self.last_alert_time[device_id]:
            return False
        
        config = self.get_device_config(device_id)
        cooldown_sec = config["alert_cooldown_minutes"] * 60
        return (time.time() - self.last_alert_time[device_id][alert_type]) < cooldown_sec
    
    def analyze_temperature_status(self, device_id, temperature, timestamp):
        """Analyze temperature using util function and handle alerts"""
        config = self.get_device_config(device_id)
        
        # Use util function for evaluation
        eval_result = evaluate_temperature(temperature, config["temp_min_celsius"], config["temp_max_celsius"])
        status = eval_result["status"]
        
        if device_id not in self.device_status: self.device_status[device_id] = {}
        prev_status = self.device_status[device_id].get("temp_status", "normal")
        self.device_status[device_id]["temp_status"] = status
        
        print(f"[TEMP] {device_id}: {temperature:.1f}°C - Status: {status}")
        
        if eval_result["alert_type"] and config["enable_malfunction_alerts"]:
            if prev_status == "normal" or not self.is_cooldown_active(device_id, eval_result["alert_type"]):
                self.send_malfunction_alert(device_id, eval_result["alert_type"], 
                                          eval_result["message"], temperature, 
                                          eval_result["severity"], eval_result["action"], timestamp)
    
    def analyze_humidity_status(self, device_id, humidity, timestamp):
        """Analyze humidity using util function and handle alerts"""
        config = self.get_device_config(device_id)
        
        # Use util function
        eval_result = evaluate_humidity(humidity, config["humidity_max_percent"])
        status = eval_result["status"]
        
        if device_id not in self.device_status: self.device_status[device_id] = {}
        prev_status = self.device_status[device_id].get("humidity_status", "normal")
        self.device_status[device_id]["humidity_status"] = status
        
        print(f"[HUMIDITY] {device_id}: {humidity:.1f}% - Status: {status}")
        
        if eval_result["alert_type"] and config["enable_malfunction_alerts"]:
            if prev_status == "normal" or not self.is_cooldown_active(device_id, eval_result["alert_type"]):
                self.send_malfunction_alert(device_id, eval_result["alert_type"], 
                                          eval_result["message"], humidity, 
                                          eval_result["severity"], eval_result["action"], timestamp)
    
    def detect_malfunction_patterns(self, device_id):
        """Detect complex patterns using util function"""
        if device_id not in self.last_readings: return
        readings = self.last_readings[device_id]
        if not readings.get("temperature") or not readings.get("humidity"): return
        
        config = self.get_device_config(device_id)
        
        # Use util function
        pattern = evaluate_complex_patterns(readings["temperature"], readings["humidity"], config)
        
        if pattern:
            alert_type = pattern["alert_type"]
            if not self.is_cooldown_active(device_id, alert_type):
                self.send_malfunction_alert(device_id, alert_type, 
                                          pattern["message"], 
                                          {"temperature": readings["temperature"], "humidity": readings["humidity"]},
                                          pattern["severity"], pattern["action"], readings["timestamp"])

    def send_malfunction_alert(self, device_id, alert_type, message, sensor_value, severity, recommended_action, timestamp):
        """Send alert via MQTT"""
        if not self.connected or not self.mqtt_client: return
        
        alert_topic = f"Group17/SmartChill/{device_id}/Alerts/Malfunction"
        alert_payload = {
            "alert_type": alert_type,
            "device_id": device_id,
            "message": message,
            "sensor_values": sensor_value if isinstance(sensor_value, dict) else {alert_type.split('_')[0]: sensor_value},
            "severity": severity,
            "timestamp": format_timestamp(timestamp),
            "service": self.service_id,
            "recommended_action": recommended_action
        }
        
        try:
            self.mqtt_client.myPublish(alert_topic, alert_payload)
            if device_id not in self.last_alert_time: self.last_alert_time[device_id] = {}
            self.last_alert_time[device_id][alert_type] = time.time()
            print(f"[ALERT] Sent for {device_id}: {alert_type}")
        except Exception as e: print(f"[ALERT] Error: {e}")

    # ===================== MQTT & Lifecycle =====================

    def notify(self, topic, payload):
        """MQTT Callback"""
        try:
            if "config_update" in topic:
                self.handle_config_update(topic, payload)
                return
            
            # Use util function
            parsed_data = parse_senml_payload(payload)
            if not parsed_data: return
            
            topic_parts = topic.split('/')
            topic_device_id = topic_parts[-2] if len(topic_parts) >= 5 else None
            
            for entry in parsed_data:
                device_id = entry["device_id"] or topic_device_id
                name = entry["sensor_name"]
                value = entry["value"]
                
                if name not in ["temperature", "humidity"]: continue
                
                if device_id not in self.known_devices:
                    if not self.check_device_exists_in_catalog(device_id): continue
                
                ts = datetime.fromtimestamp(entry["timestamp"], tz=timezone.utc)
                
                if device_id not in self.last_readings: self.last_readings[device_id] = {}
                self.last_readings[device_id][name] = float(value)
                self.last_readings[device_id]["timestamp"] = ts
                
                if name == "temperature":
                    self.analyze_temperature_status(device_id, float(value), ts)
                elif name == "humidity":
                    self.analyze_humidity_status(device_id, float(value), ts)
            
            # Check complex patterns after update
            for entry in parsed_data:
                did = entry["device_id"] or topic_device_id
                if did in self.known_devices:
                    self.detect_malfunction_patterns(did)
                    break
                    
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
            else: print(f"[INIT] Failed load devices: {response.status_code}")
        except Exception as e: print(f"[INIT] Catalog error: {e}")

    def periodic_registration(self):
        interval = self.settings["catalog"]["registration_interval_seconds"]
        while self.running:
            time.sleep(interval)
            if self.running: self.register_with_catalog()

    def status_monitor_loop(self):
        while self.running:
            try:
                time.sleep(self.settings["catalog"]["ping_interval_seconds"])
                if self.device_status:
                    print(f"[STATUS] Monitoring {len(self.device_status)} devices")
            except: pass

    def run(self):
        print("="*60 + "\n    SMARTCHILL STATUS CONTROL\n" + "="*60)
        self.register_with_catalog()
        self.load_known_devices_from_catalog()
        if not self.setup_mqtt(): return
        
        threading.Thread(target=self.periodic_registration, daemon=True).start()
        threading.Thread(target=self.status_monitor_loop, daemon=True).start()
        
        try:
            while self.running: time.sleep(1)
        except KeyboardInterrupt: self.shutdown()

    def shutdown(self):
        print("[SHUTDOWN] Stopping service..."); self.running = False
        if self.mqtt_client: self.mqtt_client.stop()