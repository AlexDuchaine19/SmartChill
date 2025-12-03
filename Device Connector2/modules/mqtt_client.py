import time
import json
from MyMQTT import MyMQTT

class MQTTClient:
    def __init__(self, settings, device_id, service):
        self.settings = settings
        self.device_id = device_id
        self.service = service
        self.broker_host = settings["mqtt_data"]["broker"]
        self.broker_port = settings["mqtt_data"]["port"]
        self.client_id = f"fridge_{device_id}_{int(time.time())}"
        self.client = MyMQTT(self.client_id, self.broker_host, self.broker_port, self)
        self.connected = False

    def start(self):
        """Setup MQTT client and connect to broker"""
        print(f"[MQTT] Connecting to broker {self.broker_host}:{self.broker_port}...")
        
        try:
            self.client.start()
            time.sleep(2)
            self.connected = True
            
            # Subscribe to config topic
            config_topic = self.service.build_command_topic("update_config")
            self.client.mySubscribe(config_topic)
            print(f"[MQTT] Subscribed to: {config_topic}")
            
            # Subscribe to simulation commands topic
            simulation_topic = self.service.build_command_topic("simulation")
            self.client.mySubscribe(simulation_topic)
            print(f"[MQTT] Subscribed to: {simulation_topic}")
            
            print("[MQTT] Connected successfully")
            return True
                
        except Exception as e:
            print(f"[MQTT] Connection error: {e}")
            return False

    def stop(self):
        """Stop MQTT client"""
        if self.client:
            self.client.stop()
            self.connected = False
            print("[MQTT] Connection closed")

    def notify(self, topic, payload):
        """Callback for incoming messages"""
        self.service.handle_message(topic, payload)

    def publish(self, topic, payload):
        """Publish message to topic"""
        if self.connected:
            try:
                self.client.myPublish(topic, payload)
            except Exception as e:
                print(f"[MQTT] Error publishing to {topic}: {e}")
