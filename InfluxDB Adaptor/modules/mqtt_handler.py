import json
import time
from datetime import datetime, timezone
from modules.utils import save_settings

class MQTTHandler:
    def __init__(self, adaptor):
        self.adaptor = adaptor
        self.settings = adaptor.settings
        self.influx_client = adaptor.influx_client
        self.mqtt_client = None

    def parse_senml_payload(self, payload):
        """Parse SenML formatted payload and extract sensor data"""
        try:
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8")

            senml_data = json.loads(payload) if isinstance(payload, str) else payload
                
            if not isinstance(senml_data, dict) or "e" not in senml_data:
                print(f"[SENML] Invalid SenML structure - missing 'e' array")
                return None
            
            base_name = senml_data.get("bn", "")
            base_time = senml_data.get("bt", 0)
            entries = senml_data.get("e", [])
            
            device_id = base_name.rstrip("/") if base_name.endswith("/") else None
            
            parsed_data = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                
                sensor_name = entry.get("n")
                value = entry.get("v")
                string_value = entry.get("vs")
                time_offset = entry.get("t", 0)
                timestamp = base_time + time_offset
                
                if sensor_name and (value is not None or string_value is not None):
                    parsed_data.append({
                        "device_id": device_id,
                        "sensor_name": sensor_name,
                        "value": value,
                        "string_value": string_value,
                        "timestamp": timestamp
                    })
            
            return parsed_data
            
        except (json.JSONDecodeError, TypeError) as e:
            print(f"[SENML] Error parsing SenML payload: {e}")
            return None

    def handle_door_event_senml(self, device_id, parsed_data):
        """Handle SenML door event data"""
        door_state = None
        duration = None
        timestamp = None
        
        for data_entry in parsed_data:
            if data_entry["sensor_name"] == "door_state":
                door_state = data_entry["string_value"]
                timestamp = data_entry["timestamp"]
            elif data_entry["sensor_name"] == "door_duration":
                duration = data_entry["value"]
        
        if door_state == "door_opened":
            print(f"[DOOR] Door OPENED for {device_id}")
            ts = datetime.fromtimestamp(timestamp, tz=timezone.utc) if timestamp else datetime.now(timezone.utc)
            self.influx_client.store_door_event(device_id, "door_opened", 1, ts)
            
        elif door_state == "door_closed":
            print(f"[DOOR] Door CLOSED for {device_id}")
            ts = datetime.fromtimestamp(timestamp, tz=timezone.utc) if timestamp else datetime.now(timezone.utc)
            self.influx_client.store_door_event(device_id, "door_closed", 0, ts, duration)

    def handle_config_update(self, topic, payload):
        """Handle configuration update via MQTT"""
        try:
            message = json.loads(payload)
            
            if message.get("type") == "influx_config_update":
                new_config = message.get("config", {})
                
                if new_config:
                    with self.adaptor.config_lock:
                        if "influxdb" in new_config:
                            self.settings["influxdb"].update(new_config["influxdb"])
                        if "defaults" in new_config:
                            self.settings["defaults"].update(new_config["defaults"])
                        
                        save_settings(self.settings, self.adaptor.settings_file)
                    
                    ack_topic = f"Group17/SmartChill/InfluxDBAdaptor/config_ack"
                    ack_payload = {
                        "status": "updated",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "config_version": self.settings["configVersion"]
                    }
                    self.mqtt_client.myPublish(ack_topic, ack_payload)
                    
        except Exception as e:
            print(f"[CONFIG] Error processing config update: {e}")

    def notify(self, topic, payload):
        """Callback method for MyMQTT"""
        try:
            if "config_update" in topic:
                self.handle_config_update(topic, payload)
                return
            
            parsed_data = self.parse_senml_payload(payload)
            if not parsed_data:
                print(f"[SENML] Failed to parse SenML data from topic: {topic}")
                return
            
            topic_parts = topic.split('/')
            
            if len(topic_parts) >= 5:
                topic_device_id = topic_parts[-2]
                topic_type = topic_parts[-1]
                
                for data_entry in parsed_data:
                    device_id = data_entry["device_id"] or topic_device_id
                    
                    if device_id not in self.adaptor.known_devices:
                        print(f"[NEW_DEVICE] Unknown device detected: {device_id}")
                        if self.adaptor.check_device_exists_in_catalog(device_id):
                            print(f"[NEW_DEVICE] Device {device_id} confirmed in catalog")
                        else:
                            print(f"[NEW_DEVICE] Device {device_id} not registered in catalog - ignoring data")
                            continue
                    
                    if topic_type == "door_event":
                        self.handle_door_event_senml(device_id, parsed_data)
                        break
                    
                    else:
                        sensor_name = data_entry["sensor_name"]
                        value = data_entry["value"]
                        timestamp = data_entry["timestamp"]
                        
                        if sensor_name == topic_type and value is not None:
                            if timestamp:
                                try:
                                    ts = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                                except (ValueError, TypeError):
                                    ts = datetime.now(timezone.utc)
                            else:
                                ts = datetime.now(timezone.utc)
                            
                            success = self.influx_client.store_sensor_data(device_id, sensor_name, value, ts)
                            if success:
                                print(f"[SENML] Stored {sensor_name} data from {device_id}: {value}")
                            else:
                                print(f"[SENML] Failed to store {sensor_name} data from {device_id}")
            else:
                print(f"[WARN] Unexpected topic format: {topic}")
                
        except Exception as e:
            print(f"[ERROR] Error processing SenML message: {e}")
            import traceback
            traceback.print_exc()
