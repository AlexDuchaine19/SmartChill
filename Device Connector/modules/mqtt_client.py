import time
import json
from MyMQTT import MyMQTT
from modules.utils import save_settings, create_senml_payload, create_door_event_senml_payload, get_sensor_unit

class MQTTClient:
    def __init__(self, settings, simulator):
        self.settings = settings
        self.simulator = simulator
        self.broker_host = settings["mqtt_data"]["broker"]
        self.broker_port = settings["mqtt_data"]["port"]
        self.topic_template = settings["mqtt_data"]["topic_template"]
        self.heartbeat_topic_template = settings["telemetry"]["heartbeat_topic"]
        self.include_events = settings["mqtt_data"].get("include_events", [])
        
        self.mqtt_client = None
        self.connected = False
        self.device_id = None

    def set_device_id(self, device_id):
        self.device_id = device_id

    def start(self):
        """Setup MQTT client and connect to broker"""
        if not self.device_id:
            print("[MQTT] Cannot start: Device ID not set")
            return False

        print(f"[MQTT] Connecting to broker {self.broker_host}:{self.broker_port}...")
        
        try:
            client_id = f"fridge_{self.device_id}_{int(time.time())}"
            self.mqtt_client = MyMQTT(client_id, self.broker_host, self.broker_port, self)
            
            # Start connection
            self.mqtt_client.start()
            time.sleep(2)
            self.connected = True
            
            # Subscribe to config topic
            config_topic = self.build_command_topic("update_config")
            self.mqtt_client.mySubscribe(config_topic)
            print(f"[MQTT] Subscribed to: {config_topic}")
            
            # Subscribe to simulation commands topic
            simulation_topic = self.build_command_topic("simulation")
            self.mqtt_client.mySubscribe(simulation_topic)
            print(f"[MQTT] Subscribed to: {simulation_topic}")
            
            print("[MQTT] Connected successfully")
            return True
                
        except Exception as e:
            print(f"[MQTT] Connection error: {e}")
            return False

    def stop(self):
        if self.mqtt_client:
            self.mqtt_client.stop()
            self.connected = False

    def build_topic(self, sensor_or_event):
        """Build MQTT topic using template from settings"""
        if not self.device_id:
            return None
            
        return self.topic_template.format(
            model=self.settings["deviceInfo"]["model"],
            device_id=self.device_id,
            sensor=sensor_or_event
        )
    
    def build_heartbeat_topic(self):
        """Build heartbeat topic using template from settings"""
        if not self.device_id:
            return None
            
        return self.heartbeat_topic_template.format(
            model=self.settings["deviceInfo"]["model"],
            device_id=self.device_id
        )
    
    def build_command_topic(self, command_type):
        """Build command topic for receiving simulation commands"""
        if not self.device_id:
            return None
        return f"Group17/SmartChill/Commands/{self.device_id}/{command_type}"
    
    def build_response_topic(self):
        """Build response topic for sending command confirmations"""
        if not self.device_id:
            return None
        return f"Group17/SmartChill/Response/{self.device_id}/command_result"

    def notify(self, topic, payload):
        """Callback method for MyMQTT - handles incoming messages"""
        try:
            message = json.loads(payload)
            
            # Handle config updates
            if "update_config" in topic:
                print(f"[MQTT] Received config update: {message}")
                
                if "sampling_intervals" in message:
                    old_intervals = self.settings.get("sampling_intervals", {}).copy()
                    self.settings["sampling_intervals"] = self.settings.get("sampling_intervals", {})
                    self.settings["sampling_intervals"].update(message["sampling_intervals"])
                    
                    print(f"[CONFIG] Updated sampling intervals:")
                    for sensor in message["sampling_intervals"]:
                        old_val = old_intervals.get(sensor, "unknown")
                        new_val = self.settings["sampling_intervals"][sensor]
                        print(f"[CONFIG]   {sensor}: {old_val}s -> {new_val}s")
                    
                    save_settings(self.settings)
            
            # Handle simulation commands
            elif "simulation" in topic:
                print(f"[MQTT] Received simulation command: {message}")
                self.simulator.handle_simulation_command(message)
                    
        except Exception as e:
            print(f"[MQTT] Error processing message: {e}")

    def publish_sensor_data(self, sensor_type, value, timestamp=None):
        """Publish sensor data in SenML format"""
        if not self.connected or not self.device_id:
            return

        topic = self.build_topic(sensor_type)
        if topic:
            unit = get_sensor_unit(sensor_type)
            senml_payload = create_senml_payload(self.device_id, sensor_type, value, unit, timestamp)
            self.mqtt_client.myPublish(topic, senml_payload)
            return topic
        return None

    def publish_door_event(self, event_type, duration=None, timestamp=None):
        """Publish door event in SenML format"""
        if not self.connected or not self.device_id or "door_event" not in self.include_events:
            return

        topic = self.build_topic("door_event")
        if topic:
            senml_payload = create_door_event_senml_payload(self.device_id, event_type, duration, timestamp)
            self.mqtt_client.myPublish(topic, senml_payload)
            print(f"[EVENT] Door {event_type.upper()} - SenML notification sent to {topic}")

    def publish_heartbeat(self, uptime):
        """Publish heartbeat message in SenML format"""
        if not self.connected or not self.device_id:
            return
            
        heartbeat_topic = self.build_heartbeat_topic()
        if heartbeat_topic:
            current_time = time.time()
            base_name = f"{self.device_id}/"
            
            senml_payload = {
                "bn": base_name,
                "bt": current_time,
                "e": [{
                    "n": "heartbeat",
                    "vs": "alive",
                    "t": 0
                }, {
                    "n": "uptime",
                    "v": uptime,
                    "u": "s",
                    "t": 0
                }]
            }
            
            self.mqtt_client.myPublish(heartbeat_topic, senml_payload)
            print(f"[HEARTBEAT] Published SenML to {heartbeat_topic}")

    def send_command_response(self, command, success, message, data=None):
        """Send response to command via MQTT"""
        try:
            response_topic = self.build_response_topic()
            if response_topic and self.connected:
                
                response = {
                    "command": command,
                    "success": success,
                    "message": message,
                    "timestamp": time.time(), # Simplified timestamp
                    "device_id": self.device_id
                }
                
                if data:
                    response["data"] = data
                
                self.mqtt_client.myPublish(response_topic, response)
                print(f"[RESPONSE] Sent: {command} -> {success} ({message})")
                
        except Exception as e:
            print(f"[RESPONSE] Error sending command response: {e}")
