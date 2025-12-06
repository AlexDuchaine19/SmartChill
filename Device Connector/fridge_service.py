import requests
import json
import time
import threading
import random
from datetime import datetime, timezone
from MyMQTT import MyMQTT

from fridge_utils import (
    load_settings, 
    save_settings_to_file,
    build_topic,
    build_heartbeat_topic,
    build_command_topic,
    build_response_topic,
    create_senml_payload,
    create_door_event_senml_payload,
    get_door_open_probability,
    get_sensor_unit
)

SETTINGS_FILE = "settings.json"

class FridgeSimulator:
    def __init__(self):
        # Load settings
        self.settings = load_settings(SETTINGS_FILE)
        
        self.mac_address = self.settings["deviceInfo"]["mac_address"]
        self.model = self.settings["deviceInfo"]["model"]
        self.firmware_version = self.settings["deviceInfo"]["firmware_version"]
        self.device_id = self.settings["deviceInfo"].get("deviceID")  
        
        # Sensor configuration
        self.sensors = {}
        for sensor in self.settings["deviceInfo"]["sensors"]:
            if sensor == "temperature":
                self.sensors[sensor] = 4.0
            elif sensor == "humidity":
                self.sensors[sensor] = 55.0
            elif sensor == "gas":
                self.sensors[sensor] = 20.0
            elif sensor == "light":
                self.sensors[sensor] = 5.0
        
        # MQTT configuration from settings
        self.broker_host = self.settings["mqtt_data"]["broker"]
        self.broker_port = self.settings["mqtt_data"]["port"]
        self.topic_template = self.settings["mqtt_data"]["topic_template"]
        self.include_events = self.settings["mqtt_data"].get("include_events", [])
        
        # Telemetry configuration
        self.publish_qos = self.settings["telemetry"].get("publish_qos", 1)
        self.retain = self.settings["telemetry"].get("retain", False)
        self.heartbeat_topic_template = self.settings["telemetry"]["heartbeat_topic"]
        self.heartbeat_interval = self.settings["telemetry"]["heartbeat_interval_s"]
        
        # Sampling intervals
        self.sampling_intervals = self.settings.get("sampling_intervals", {})
        self.last_publish = {sensor: 0 for sensor in self.sensors.keys()}
        
        # MQTT client
        self.mqtt_client = None
        self.connected = False
        
        # Simulation state
        self.running = True
        self.door_open = False
        self.spoilage_active = False
        self.malfunction_active = False
        self.automatic_mode = True
        self.manual_door_control = False 
        
        # Door management
        self.door_open_start_time = None
        
        # Thermal model parameters
        self.target_temperature = 4.0
        self.compressor_on = False
        self.last_temp_update = time.time()
        
        # Cooling/Warming rates (°C per hour)
        self.cooling_rate = 3.0          
        self.warming_rate = 0.5          
        self.door_warming_rate = 3.0     
        
        # Compressor thresholds
        self.temp_min = 3.5              
        self.temp_max = 4.5              
        
        # Initialize temperature near target
        self.sensors["temperature"] = self.target_temperature + random.uniform(-0.2, 0.2)
        
        # Initialize compressor stats
        self.compressor_last_change = time.time()
        self.min_cycle_time = 300

    def save_settings(self):
        """Helper to save current settings using util function"""
        self.settings["deviceInfo"]["deviceID"] = self.device_id
        self.settings["sampling_intervals"] = self.sampling_intervals
        self.settings["last_config_sync"] = datetime.now(timezone.utc).isoformat()
        save_settings_to_file(self.settings, SETTINGS_FILE)

    def register_with_catalog(self):
        """Register device with catalog service"""
        print(f"[REG] Registering device {self.mac_address} with model {self.model}...")
        
        registration_data = {
            "mac_address": self.mac_address,
            "model": self.model,
            "firmware_version": self.firmware_version,
            "sensors": self.settings["deviceInfo"]["sensors"]
        }
        
        try:
            response = requests.post(
                f"{self.settings['catalog_url']}/devices/register",
                json=registration_data,
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            
            if response.status_code in [200, 201]:
                config = response.json()
                self.device_id = config["device_id"]
                
                print(f"[REG] Registration successful: {config['status']}")
                print(f"[REG] Device ID: {self.device_id}")
                
                # Save updated settings
                self.save_settings()
                return True
                
            else:
                print(f"[REG] Registration failed: {response.status_code}")
                return False
                
        except requests.RequestException as e:
            print(f"[REG] Failed to connect to catalog: {e}")
            print("[REG] Proceeding with local configuration...")
            self.device_id = f"SmartChill_{self.mac_address.replace(':', '')}"
            return False
    
    def setup_mqtt(self):
        """Setup MQTT client and connect to broker"""
        print(f"[MQTT] Connecting to broker {self.broker_host}:{self.broker_port}...")
        
        try:
            client_id = f"fridge_{self.device_id}_{int(time.time())}"
            self.mqtt_client = MyMQTT(client_id, self.broker_host, self.broker_port, self)
            
            # Start connection
            self.mqtt_client.start()
            time.sleep(2)
            self.connected = True
            
            # Subscribe topics
            config_topic = build_command_topic(self.device_id, "update_config")
            self.mqtt_client.mySubscribe(config_topic)
            print(f"[MQTT] Subscribed to: {config_topic}")
            
            simulation_topic = build_command_topic(self.device_id, "simulation")
            self.mqtt_client.mySubscribe(simulation_topic)
            print(f"[MQTT] Subscribed to: {simulation_topic}")
            
            print("[MQTT] Connected successfully")
            return True
                
        except Exception as e:
            print(f"[MQTT] Connection error: {e}")
            return False
    
    def notify(self, topic, payload):
        """Callback method for MyMQTT"""
        try:
            message = json.loads(payload)
            
            if "update_config" in topic:
                print(f"[MQTT] Received config update: {message}")
                if "sampling_intervals" in message:
                    self.sampling_intervals.update(message["sampling_intervals"])
                    self.save_settings()
            
            elif "simulation" in topic:
                print(f"[MQTT] Received simulation command: {message}")
                self._handle_simulation_command(message)
                    
        except Exception as e:
            print(f"[MQTT] Error processing message: {e}")
    
    def _handle_simulation_command(self, command):
        """Handle simulation control commands"""
        try:
            action = command.get("action", "").lower()
            
            if action == "door_open":
                 self.manual_door_control = True
                 self._simulate_door_open()
                 self._send_command_response("manual_door_on", True, "Manual door control ENABLED")
            
            elif action == "door_close":
                 self.manual_door_control = False
                 self._simulate_door_close()
                 self._send_command_response("manual_door_off", True, "Manual door control DISABLED")

            elif action == "spoilage_start":
                self.spoilage_active = True
                self._send_command_response("spoilage_start", True, "Spoilage simulation activated")
                
            elif action == "spoilage_stop":
                self.spoilage_active = False
                self._send_command_response("spoilage_stop", True, "Spoilage simulation deactivated")
                
            elif action == "malfunction_start":
                self.malfunction_active = True
                self._send_command_response("malfunction_start", True, "Malfunction simulation activated")
                
            elif action == "malfunction_stop":
                self.malfunction_active = False
                self._send_command_response("malfunction_stop", True, "Malfunction simulation deactivated")
                
            elif action == "reset":
                self.spoilage_active = False
                self.malfunction_active = False
                if self.door_open:
                    self._simulate_door_close()
                self.sensors["temperature"] = self.target_temperature
                self._send_command_response("reset", True, "Simulator reset to normal")
                
            elif action == "status":
                status = self.get_simulation_status()
                self._send_command_response("status", True, "Status retrieved", status)
                
            else:
                self._send_command_response("unknown", False, f"Unknown command: {action}")
                
        except Exception as e:
            print(f"[CMD] Error handling simulation command: {e}")
            self._send_command_response("error", False, f"Command processing error: {str(e)}")
    
    def _send_command_response(self, command, success, message, data=None):
        """Send response to command via MQTT"""
        try:
            response_topic = build_response_topic(self.device_id)
            if response_topic and self.mqtt_client and self.connected:
                
                response = {
                    "command": command,
                    "success": success,
                    "message": message,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "device_id": self.device_id
                }
                
                if data:
                    response["data"] = data
                
                self.mqtt_client.myPublish(response_topic, response)
                print(f"[RESPONSE] Sent: {command} -> {success} ({message})")
                
        except Exception as e:
            print(f"[RESPONSE] Error sending command response: {e}")
    
    def get_simulation_status(self):
        """Get current simulation status"""
        return {
            "device_id": self.device_id,
            "door_open": self.door_open,
            "spoilage_active": self.spoilage_active,
            "malfunction_active": self.malfunction_active,
            "compressor_on": self.compressor_on,
            "current_temperature": round(self.sensors["temperature"], 2),
            "automatic_mode": self.automatic_mode
        }
    
    def update_compressor_state(self):
        """Physics: Check compressor state based on temperature"""
        temp = self.sensors["temperature"]
        if not self.malfunction_active:
            if not self.compressor_on and temp >= self.temp_max:
                self.compressor_on = True
                self.compressor_last_change = time.time()
                print(f"[THERMAL] Compressor ON - Temp: {temp:.2f}°C")
            elif self.compressor_on and temp <= self.temp_min:
                self.compressor_on = False
                self.compressor_last_change = time.time()
                print(f"[THERMAL] Compressor OFF - Temp: {temp:.2f}°C")
    
    def simulate_thermal_dynamics(self):
        """Physics: Calculate new temperature"""
        current_time = time.time()
        time_delta = current_time - self.last_temp_update
        self.last_temp_update = current_time
        dt_hours = time_delta / 3600.0
        
        current_temp = self.sensors["temperature"]
        temp_change = 0
        
        if self.malfunction_active:
            temp_change = self.warming_rate * 3 * dt_hours
        elif self.door_open:
            temp_change = self.door_warming_rate * dt_hours
        elif self.compressor_on:
            temp_change = -self.cooling_rate * dt_hours
        else:
            temp_change = self.warming_rate * dt_hours
        
        new_temp = current_temp + temp_change
        
        # Safety limits
        if self.malfunction_active:
            new_temp = max(-5.0, min(25.0, new_temp))
        else:
            new_temp = max(0.0, min(10.0, new_temp))
        
        self.sensors["temperature"] = new_temp

    def generate_realistic_data(self):
        """Main simulation step"""
        current_time = time.time()
        
        # 1. Physics
        self.simulate_thermal_dynamics()
        self.update_compressor_state()
        
        # 2. Other sensors
        if self.door_open:
            self.sensors["humidity"] = min(90, self.sensors["humidity"] + random.uniform(0.1, 0.5))
            self.sensors["light"] = random.uniform(120, 200)
        else:
            target_humidity = 65 - (self.sensors["temperature"] - 4) * 3
            diff = (target_humidity - self.sensors["humidity"]) * 0.05
            self.sensors["humidity"] += diff + random.uniform(-0.5, 0.5)
            self.sensors["humidity"] = max(30, min(85, self.sensors["humidity"]))
            self.sensors["light"] = random.uniform(0, 8)
        
        # Gas sensor
        if self.spoilage_active:
            self.sensors["gas"] = random.uniform(450, 700)
        else:
            self.sensors["gas"] = random.uniform(8, 45)
        
        # 3. Automatic door
        if (self.automatic_mode and not self.door_open and 
            random.random() < get_door_open_probability()):
            self._simulate_door_open()
        
        if (self.door_open and not self.manual_door_control and
            self.door_open_start_time and 
            (current_time - self.door_open_start_time) > random.uniform(20, 120)):
            self._simulate_door_close()
    
    def _simulate_door_open(self):
        """Simulate door opening"""
        if not self.door_open:
            self.door_open = True
            self.door_open_start_time = time.time()
            
            if "door_event" in self.include_events:
                senml_payload = create_door_event_senml_payload(
                    self.device_id, "door_opened", timestamp=self.door_open_start_time
                )
                topic = build_topic(self.topic_template, self.model, self.device_id, "door_event")
                if self.mqtt_client and self.connected and topic:
                    self.mqtt_client.myPublish(topic, senml_payload)
                    print(f"[EVENT] Door OPENED - Notification sent")
    
    def _simulate_door_close(self):
        """Simulate door closing"""
        if self.door_open:
            current_time = time.time()
            duration = current_time - self.door_open_start_time if self.door_open_start_time else 0
            self.door_open = False
            self.door_open_start_time = None
            
            if "door_event" in self.include_events:
                senml_payload = create_door_event_senml_payload(
                    self.device_id, "door_closed", duration=duration, timestamp=current_time
                )
                topic = build_topic(self.topic_template, self.model, self.device_id, "door_event")
                if self.mqtt_client and self.connected and topic:
                    self.mqtt_client.myPublish(topic, senml_payload)
                    print(f"[EVENT] Door CLOSED after {duration:.1f}s - Notification sent")
    
    def publish_sensor_data(self):
        """Publish sensor data"""
        if not self.connected or not self.mqtt_client or not self.device_id:
            return
            
        current_time = time.time()
        for sensor_type, value in self.sensors.items():
            interval = self.sampling_intervals.get(sensor_type, 60)
            if current_time - self.last_publish[sensor_type] >= interval:
                
                topic = build_topic(self.topic_template, self.model, self.device_id, sensor_type)
                
                if topic:
                    senml_payload = create_senml_payload(self.device_id, sensor_type, value, current_time)
                    self.mqtt_client.myPublish(topic, senml_payload)
                    self.last_publish[sensor_type] = current_time
                    
                    unit = get_sensor_unit(sensor_type)
                    print(f"[PUB] {sensor_type}: {value:.2f}{unit} -> {topic}")

    def publish_heartbeat(self):
        """Publish heartbeat"""
        if not self.connected or not self.mqtt_client or not self.device_id:
            return
            
        heartbeat_topic = build_heartbeat_topic(self.heartbeat_topic_template, self.model, self.device_id)
        if heartbeat_topic:
            current_time = time.time()
            base_name = f"{self.device_id}/"
            senml_payload = {
                "bn": base_name, "bt": current_time,
                "e": [{"n": "heartbeat", "vs": "alive", "t": 0}, 
                      {"n": "uptime", "v": current_time, "u": "s", "t": 0}]
            }
            self.mqtt_client.myPublish(heartbeat_topic, senml_payload)
            print(f"[HEARTBEAT] Published to {heartbeat_topic}")

    def print_status(self):
        """Print status to console"""
        print(f"\n[STATUS] Device: {self.device_id} | Door: {'OPEN' if self.door_open else 'CLOSED'} | "
              f"Comp: {'ON' if self.compressor_on else 'OFF'} | Temp: {self.sensors['temperature']:.2f}°C")

    # Loops
    def sensor_simulation_loop(self):
        while self.running:
            self.generate_realistic_data()
            time.sleep(2)

    def mqtt_publish_loop(self):
        while self.running:
            self.publish_sensor_data()
            time.sleep(5)

    def heartbeat_loop(self):
        while self.running:
            self.publish_heartbeat()
            time.sleep(self.heartbeat_interval)
            
    def status_loop(self):
        while self.running:
            time.sleep(300)
            self.print_status()

    def run(self):
        """Main execution"""
        print(f"=== FRIDGE SIMULATOR ({self.model}) ===")
        
        if not self.register_with_catalog():
            print("[WARN] Using local configuration")
        
        if not self.setup_mqtt():
            print("[FATAL] MQTT Setup failed")
            return
            
        threads = [
            threading.Thread(target=self.sensor_simulation_loop, daemon=True),
            threading.Thread(target=self.mqtt_publish_loop, daemon=True),
            threading.Thread(target=self.heartbeat_loop, daemon=True),
            threading.Thread(target=self.status_loop, daemon=True)
        ]
        for t in threads: t.start()
        
        try:
            while self.running: time.sleep(1)
        except KeyboardInterrupt:
            self.shutdown()

    def shutdown(self):
        print("[SHUTDOWN] Stopping simulator...")
        self.running = False
        if self.mqtt_client:
            self.mqtt_client.stop()