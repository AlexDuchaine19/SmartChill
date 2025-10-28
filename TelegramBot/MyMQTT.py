import paho.mqtt.client as PahoMQTT
import json
import time # Import time module

class MyMQTT:
    def __init__(self, clientID, broker, port, notifier=None):
        self.broker = broker
        self.port = port
        self.notifier = notifier
        self.clientID = clientID
        self._topic = ""
        self._isSubscriber = False
        # ---- New flag ----
        self._isConnected = False 
        # ------------------
        
        self._paho_mqtt = PahoMQTT.Client(callback_api_version=PahoMQTT.CallbackAPIVersion.VERSION1,
                                          client_id=clientID,
                                          clean_session=True) 
        
        self._paho_mqtt.on_connect = self.myOnConnect
        self._paho_mqtt.on_message = self.myOnMessageReceived
        # Optional: Add on_disconnect for better debugging
        self._paho_mqtt.on_disconnect = self.myOnDisconnect 
 
 
    def myOnConnect (self, paho_mqtt, userdata, flags, rc):
        if rc == 0:
            print(f"Connected to {self.broker} with result code: {rc}", flush=True)
            # ---- Set flag on success ----
            self._isConnected = True
            # ---------------------------
        else:
            print(f"Failed to connect to {self.broker}. Error code: {rc}", flush=True)
            # ---- Ensure flag is false on failure ----
            self._isConnected = False
            # ---------------------------------------

    # Optional: Callback for disconnections
    def myOnDisconnect(self, client, userdata, rc):
        print(f"Disconnected from {self.broker} with result code: {rc}", flush=True)
        self._isConnected = False # Update flag on disconnect

    def myOnMessageReceived (self, paho_mqtt , userdata, msg):
        if self.notifier:
            try: # Add try-except around notify call
                 self.notifier.notify(msg.topic, msg.payload)
            except Exception as e:
                 print(f"Error calling notifier for topic {msg.topic}: {e}", flush=True)
                 import traceback
                 traceback.print_exc() # Log full error
        else:
            print(f"Received message on {msg.topic}: {msg.payload.decode()}", flush=True) # Decode payload for printing
        
    def myPublish (self, topic, msg):
        # Check connection before publishing
        if not self._isConnected:
             print(f"WARN: Cannot publish to {topic}, MQTT not connected.", flush=True)
             return
        # Ensure msg is JSON serializable (usually a dict) before dumping
        try:
             payload = json.dumps(msg)
             self._paho_mqtt.publish(topic, payload, 2)
        except TypeError as e:
             print(f"Error: Could not serialize message for topic {topic}: {e}. Message: {msg}", flush=True)
       
    # In MyMQTT.py
    def mySubscribe (self, topic):
        # Check connection before subscribing
        if not self._isConnected:
             print(f"WARN: Cannot subscribe to {topic}, MQTT not connected.", flush=True)
             # Return False to indicate subscription was not attempted
             return False 
        try:
            # Paho subscribe returns a tuple: (result, mid)
            result, mid = self._paho_mqtt.subscribe(topic, 2) 

            if result == PahoMQTT.MQTT_ERR_SUCCESS:
                print(f"Successfully initiated subscription to {topic} (mid={mid})", flush=True)
                # Store subscribed topics in a list for proper unsubscribing later
                if isinstance(self._topic, list):
                    if topic not in self._topic: self._topic.append(topic)
                elif self._topic != topic: 
                     self._topic = [self._topic] if self._topic else []
                     if topic not in self._topic: self._topic.append(topic)
                self._isSubscriber = True
                return True # Indicate success
            else:
                 # Subscription was rejected by the broker or Paho
                 print(f"ERROR: Failed to subscribe to {topic}. Result code: {result}", flush=True)
                 # Optionally raise an exception here if you want TelegramBot.start() to fail hard
                 # raise Exception(f"MQTT subscription failed for {topic} with code {result}")
                 return False # Indicate failure

        except Exception as e:
             # Catch potential errors during the subscribe call itself (e.g., network issue)
             print(f"EXCEPTION during subscribe call for {topic}: {e}", flush=True)
             # Re-raise the exception so the caller's try-except block can catch it
             raise e
 
    def start(self):
        #manage connection to broker
        try:
            print(f"Attempting to connect to MQTT broker: {self.broker}:{self.port}", flush=True)
            self._isConnected = False # Reset flag before connection attempt
            self._paho_mqtt.connect(self.broker, self.port)
            self._paho_mqtt.loop_start()
            
            # ---- Wait for connection confirmation ----
            max_wait_time = 10 # Seconds to wait for connection
            start_time = time.time()
            while not self._isConnected and time.time() - start_time < max_wait_time:
                time.sleep(0.1) # Wait briefly
            # ----------------------------------------

            if self._isConnected:
                 print("Connection successful (within start method).", flush=True)
                 return True # <-- Return True on success
            else:
                 print("Connection attempt timed out or failed (within start method).", flush=True)
                 self._paho_mqtt.loop_stop() # Stop loop if connection failed
                 return False # <-- Return False on failure/timeout
                 
        except Exception as e:
            print(f"Exception during MQTT connect/loop_start: {e}", flush=True)
            # Ensure loop is stopped if an exception occurred before confirmation
            try: self._paho_mqtt.loop_stop()
            except: pass
            return False # <-- Return False on exception
    
    def unsubscribe(self): # Keep unsubscribe logic simple
         # Unsubscribe from all tracked topics
         topics_to_unsubscribe = []
         if isinstance(self._topic, list): topics_to_unsubscribe = self._topic
         elif self._topic: topics_to_unsubscribe = [self._topic]

         if self._isSubscriber and topics_to_unsubscribe:
            for t in topics_to_unsubscribe:
                 print(f"Unsubscribing from {t}", flush=True)
                 self._paho_mqtt.unsubscribe(t)
            self._topic = [] # Clear tracked topics
            
    def stop (self):
        if self._isSubscriber:
            self.unsubscribe() # Call the improved unsubscribe

        # Stop the network loop gracefully
        try: self._paho_mqtt.loop_stop()
        except: pass
        
        # Disconnect
        try: self._paho_mqtt.disconnect()
        except: pass

        self._isConnected = False # Update status
        print("Disconnected from MQTT broker (stop method called).", flush=True)