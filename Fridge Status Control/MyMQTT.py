import paho.mqtt.client as PahoMQTT
import json
class MyMQTT:
    def __init__(self, clientID, broker, port, notifier=None):
        self.broker = broker
        self.port = port
        #we need to give a notifier that it will use if it's a subscriber (if not no need)
        self.notifier = notifier
        self.clientID = clientID
        self._topic = ""
        self._isSubscriber = False
        # create an instance of paho.mqtt.client
        self._paho_mqtt = PahoMQTT.Client(callback_api_version=PahoMQTT.CallbackAPIVersion.VERSION1,
                                          client_id=clientID,
                                          clean_session=True) 
        # register the callback
        self._paho_mqtt.on_connect = self.myOnConnect
        self._paho_mqtt.on_message = self.myOnMessageReceived
 
 
    def myOnConnect (self, paho_mqtt, userdata, flags, rc):
        if rc == 0:
            print(f"Connected to {self.broker} with result code: {rc}", flush=True)
        else:
            print(f"Failed to connect to {self.broker}. Error code: {rc}", flush=True)

    def myOnMessageReceived (self, paho_mqtt , userdata, msg):
        # A new message is received
        # now we call the method notify from the notifier class, so when a message will be received
        # ok i need to go to on.message = myOnMessageReceived, and say ok I need to take my notifier object and its notify method
        # it will go to self.notifier = notifier and look if it has a notify method
        # Metodo di notifica quando riceviamo un messaggio
        if self.notifier:
            self.notifier.notify(msg.topic, msg.payload)
        else:
            print(f"Received message on {msg.topic}: {msg.payload}", flush=True)
        

    def myPublish (self, topic, msg):
        # publish a message with a certain topic
        self._paho_mqtt.publish(topic, json.dumps(msg), 2)
       
 
    def mySubscribe (self, topic):
        # subscribe for a topic
        self._paho_mqtt.subscribe(topic, 2) 
        # just to remember that it works also as a subscriber
        self._isSubscriber = True
        self._topic = topic
        print(f"Subscribed to {topic}", flush=True)
 
    def start(self):
        #manage connection to broker
        try:
            self._paho_mqtt.connect(self.broker, self.port)
            self._paho_mqtt.loop_start()
            print(f"Starting MQTT client with broker {self.broker} on port {self.port}", flush=True)
        except Exception as e:
            print(f"Failed to connect to MQTT broker: {e}", flush=True)
    

    def unsubscribe(self):
        if (self._isSubscriber):
            # remember to unsuscribe if it is working also as subscriber 
            self._paho_mqtt.unsubscribe(self._topic)
            
            
    def stop (self):
        if (self._isSubscriber):
            # remember to unsuscribe if it is working also as subscriber 
            self._paho_mqtt.unsubscribe(self._topic)
        self._paho_mqtt.loop_stop()
        self._paho_mqtt.disconnect()
        print("Disconnected from MQTT broker", flush=True)