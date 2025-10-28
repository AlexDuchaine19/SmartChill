import requests
import json
import time
import threading
import random
import math
from datetime import datetime, timezone
from MyMQTT import MyMQTT
import os

SETTINGS_FILE = "settings.json"

class FridgeSimulator:
    def __init__(self):
        # Load settings from JSON file
        self.settings = self._load_settings()
        
        # Device identity from settings
        self.mac_address = self.settings["deviceInfo"]["mac_address"]
        self.model = self.settings["deviceInfo"]["model"]
        self.firmware_version = self.settings["deviceInfo"]["firmware_version"]
        self.device_id = self.settings["deviceInfo"].get("deviceID")  # May be None initially
        
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
        self.manual_door_control = False # If True, door will not close automatically
        
        # Door management
        self.door_open_start_time = None
        
        # Thermal model parameters - SEMPLIFICATI
        self.target_temperature = 4.0
        self.compressor_on = False
        self.last_temp_update = time.time()
        
        # Rate di cambio temperatura (°C per ora)
        self.cooling_rate = 3.0          # Quanto veloce raffredda con compressore ON
        self.warming_rate = 0.5          # Quanto veloce scalda con compressore OFF
        self.door_warming_rate = 3.0     # Quanto veloce scalda con porta aperta
        
        # Soglie per il compressore (isteresi semplice)
        self.temp_min = 3.5              # Sotto questa temperatura: compressore OFF
        self.temp_max = 4.5              # Sopra questa temperatura: compressore ON
        
        # Inizializza temperatura vicino al target
        self.sensors["temperature"] = self.target_temperature + random.uniform(-0.2, 0.2)
        
    def _load_settings(self):
        """Load settings from JSON file"""
        if not os.path.exists(SETTINGS_FILE):
            raise FileNotFoundError(f"Settings file not found: {SETTINGS_FILE}")
        
        with open(SETTINGS_FILE, 'r') as f:
            return json.load(f)
    
    def _save_settings(self):
        """Save updated settings back to JSON file"""
        self.settings["deviceInfo"]["deviceID"] = self.device_id
        self.settings["sampling_intervals"] = self.sampling_intervals
        self.settings["last_config_sync"] = datetime.now(timezone.utc).isoformat()
        
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(self.settings, f, indent=4)
        
        print(f"[CONFIG] Settings saved to {SETTINGS_FILE}")
    
    def register_with_catalog(self):
        """Register device with catalog service using new API"""
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
                print(f"[REG] MQTT Topics: {len(config.get('mqtt_topics', []))} topics assigned")
                
                # Save updated settings
                self._save_settings()
                return True
                
            else:
                print(f"[REG] Registration failed: {response.status_code}")
                error_msg = response.json() if response.content else "Unknown error"
                print(f"[REG] Error: {error_msg}")
                return False
                
        except requests.RequestException as e:
            print(f"[REG] Failed to connect to catalog: {e}")
            print("[REG] Proceeding with local configuration...")
            # Use MAC-based device_id as fallback
            self.device_id = f"SmartChill_{self.mac_address.replace(':', '')}"
            return False
    
    def build_topic(self, sensor_or_event):
        """Build MQTT topic using template from settings"""
        if not self.device_id:
            return None
            
        return self.topic_template.format(
            model=self.model,
            device_id=self.device_id,
            sensor=sensor_or_event
        )
    
    def build_heartbeat_topic(self):
        """Build heartbeat topic using template from settings"""
        if not self.device_id:
            return None
            
        return self.heartbeat_topic_template.format(
            model=self.model,
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
    
    def create_senml_payload(self, sensor_type, value, timestamp=None):
        """Create SenML formatted payload for sensor data"""
        if timestamp is None:
            timestamp = time.time()
        
        # SenML base name for device identification
        base_name = f"{self.device_id}/"
        
        # Get sensor unit
        unit = self._get_sensor_unit(sensor_type)
        
        # Create SenML object
        senml_data = {
            "bn": base_name,  # Base name
            "bt": timestamp,  # Base time
            "e": [{
                "n": sensor_type,  # Sensor name
                "v": round(value, 2),  # Value
                "u": unit,  # Unit
                "t": 0  # Time offset from base time (0 = same time)
            }]
        }
        
        return senml_data
    
    def create_door_event_senml_payload(self, event_type, duration=None, timestamp=None):
        """Create SenML formatted payload for door events"""
        if timestamp is None:
            timestamp = time.time()
        
        base_name = f"{self.device_id}/"
        
        # Create basic event entry
        event_entry = {
            "n": "door_state",
            "vs": event_type,  # String value for event type
            "t": 0
        }
        
        # Add duration if door is closing
        entries = [event_entry]
        if event_type == "door_closed" and duration is not None:
            entries.append({
                "n": "door_duration",
                "v": round(duration, 1),
                "u": "s",
                "t": 0
            })
        
        senml_data = {
            "bn": base_name,
            "bt": timestamp,
            "e": entries
        }
        
        return senml_data
    
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
    
    def notify(self, topic, payload):
        """Callback method for MyMQTT - handles incoming messages"""
        try:
            message = json.loads(payload)
            
            # Handle config updates
            if "update_config" in topic:
                print(f"[MQTT] Received config update: {message}")
                
                if "sampling_intervals" in message:
                    old_intervals = self.sampling_intervals.copy()
                    self.sampling_intervals.update(message["sampling_intervals"])
                    
                    print(f"[CONFIG] Updated sampling intervals:")
                    for sensor in message["sampling_intervals"]:
                        old_val = old_intervals.get(sensor, "unknown")
                        new_val = self.sampling_intervals[sensor]
                        print(f"[CONFIG]   {sensor}: {old_val}s -> {new_val}s")
                    
                    self._save_settings()
            
            # Handle simulation commands
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
                 self._send_command_response("manual_door_on", True, "Manual door control ENABLED (door won't auto-close)")
                 print("[CMD] Manual door control ENABLED")
            
            elif action == "door_close":
                 self.manual_door_control = False
                 self._simulate_door_close()
                 self._send_command_response("manual_door_off", True, "Manual door control DISABLED (door will auto-close)")
                 print("[CMD] Manual door control DISABLED")

            elif action == "spoilage_start":
                self.spoilage_active = True
                self._send_command_response("spoilage_start", True, "Spoilage simulation activated")
                print("[CMD] Spoilage simulation activated via MQTT")
                
            elif action == "spoilage_stop":
                self.spoilage_active = False
                self._send_command_response("spoilage_stop", True, "Spoilage simulation deactivated")
                print("[CMD] Spoilage simulation deactivated via MQTT")
                
            elif action == "malfunction_start":
                self.malfunction_active = True
                self._send_command_response("malfunction_start", True, "Malfunction simulation activated")
                print("[CMD] Malfunction simulation activated via MQTT")
                
            elif action == "malfunction_stop":
                self.malfunction_active = False
                self._send_command_response("malfunction_stop", True, "Malfunction simulation deactivated")
                print("[CMD] Malfunction simulation deactivated via MQTT")
                
            elif action == "reset":
                self.spoilage_active = False
                self.malfunction_active = False
                if self.door_open:
                    self._simulate_door_close()
                self.sensors["temperature"] = self.target_temperature
                self._send_command_response("reset", True, "Simulator reset to normal operation")
                print("[CMD] Simulator reset to normal operation via MQTT")
                
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
            response_topic = self.build_response_topic()
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
            "target_temperature": self.target_temperature,
            "current_temperature": round(self.sensors["temperature"], 2),
            "automatic_mode": self.automatic_mode,
            "sensors": {k: round(v, 2) for k, v in self.sensors.items()},
            "last_compressor_change": self.compressor_last_change,
            "uptime": time.time() - self.compressor_last_change
        }
    
    def update_compressor_state(self):
        """Controlla il compressore con isteresi semplice"""
        temp = self.sensors["temperature"]
        
        # Solo se non c'è malfunzionamento
        if not self.malfunction_active:
            if not self.compressor_on and temp >= self.temp_max:
                # Temperatura troppo alta: accendi compressore
                self.compressor_on = True
                print(f"[THERMAL] Compressor ON - Temp: {temp:.2f}°C (>= {self.temp_max}°C)")
                
            elif self.compressor_on and temp <= self.temp_min:
                # Temperatura abbastanza bassa: spegni compressore
                self.compressor_on = False
                print(f"[THERMAL] Compressor OFF - Temp: {temp:.2f}°C (<= {self.temp_min}°C)")
    
    def simulate_thermal_dynamics(self):
        """Simula la dinamica termica in modo lineare"""
        current_time = time.time()
        time_delta = current_time - self.last_temp_update  # secondi
        self.last_temp_update = current_time
        
        # Converte in ore per i rate
        dt_hours = time_delta / 3600.0
        
        current_temp = self.sensors["temperature"]
        
        # Calcola il cambiamento di temperatura
        if self.malfunction_active:
            # Malfunzionamento: si scalda velocemente, compressore non funziona
            temp_change = self.warming_rate * 3 * dt_hours  # 3x più veloce
            
        elif self.door_open:
            # Porta aperta: si scalda velocemente indipendentemente dal compressore
            temp_change = self.door_warming_rate * dt_hours
            
        elif self.compressor_on:
            # Compressore acceso: raffredda
            temp_change = -self.cooling_rate * dt_hours  # negativo = raffreddamento
            
        else:
            # Compressore spento: si scalda lentamente
            temp_change = self.warming_rate * dt_hours
        
        # Applica il cambiamento
        new_temp = current_temp + temp_change
        
        # Limiti di sicurezza
        if self.malfunction_active:
            # Con malfunzionamento può arrivare a temperatura ambiente
            new_temp = max(-5.0, min(25.0, new_temp))
        else:
            # Funzionamento normale: limiti frigorifero
            new_temp = max(0.0, min(10.0, new_temp))
        
        self.sensors["temperature"] = new_temp
    
    def _get_door_open_probability(self):
        """
        Calculate the probability of the door opening based on the time of day and day of the week.
        This simulates realistic user behavior (e.g., more activity during meal times).
        The probability values represent the chance of opening per 2-second simulation tick.
        """
        now = datetime.now()
        current_hour = now.hour
        # Monday is 0 and Sunday is 6
        is_weekend = now.weekday() >= 5

        # (start_hour, end_hour, probability)
        # Probabilities are higher during typical meal times.
        weekday_schedule = [
            (7, 9, 0.001),     # Breakfast
            (12, 14, 0.0025),  # Lunch
            (19, 21, 0.0025)   # Dinner
        ]
        
        weekend_schedule = [
            (9, 11, 0.0015),   # Late breakfast
            (13, 15, 0.003),   # Lunch
            (19, 22, 0.003)    # Dinner
        ]

        # Use the appropriate schedule based on the day
        schedule = weekend_schedule if is_weekend else weekday_schedule
        
        # Check if the current time falls into a scheduled high-activity period
        for start, end, prob in schedule:
            if start <= current_hour < end:
                return prob
        
        # Return a very low base probability for off-peak hours
        # Lower probability during the night
        if 0 <= current_hour < 6:
            return 0.00001  # Extremely low chance during the night
        else:
            return 0.0001   # Low chance for other times

    def generate_realistic_data(self):
        """Generate realistic sensor data based on current state and thermal model"""
        current_time = time.time()
        
        # 1. First, simulate the thermal dynamics
        self.simulate_thermal_dynamics()
        
        # 2. Then, update the compressor state based on the new temperature
        self.update_compressor_state()
        
        # 3. Simulate other sensors
        # Humidity correlated with temperature and door state
        if self.door_open:
            self.sensors["humidity"] = min(90, self.sensors["humidity"] + random.uniform(0.1, 0.5))
        else:
            target_humidity = 65 - (self.sensors["temperature"] - 4) * 3
            diff = (target_humidity - self.sensors["humidity"]) * 0.05
            self.sensors["humidity"] += diff + random.uniform(-0.5, 0.5)
            self.sensors["humidity"] = max(30, min(85, self.sensors["humidity"]))
        
        # Light sensor
        if self.door_open:
            self.sensors["light"] = random.uniform(120, 200)
        else:
            self.sensors["light"] = random.uniform(0, 8)
        
        # Gas sensor
        if self.spoilage_active:
            self.sensors["gas"] = random.uniform(450, 700)
        else:
            self.sensors["gas"] = random.uniform(8, 45)
        
        # Automatic door events based on a time-dependent probability model
        if (self.automatic_mode and not self.door_open and 
            random.random() < self._get_door_open_probability()):
            self._simulate_door_open()
        
        if (self.door_open and 
            not self.manual_door_control and # <-- Add this condition
            self.door_open_start_time and 
            (current_time - self.door_open_start_time) > random.uniform(20, 120)):
            self._simulate_door_close()
    
    def _simulate_door_open(self):
        """Simulate door opening event with immediate SenML notification"""
        if not self.door_open:
            self.door_open = True
            self.door_open_start_time = time.time()
            
            # Send immediate door event in SenML format if configured
            if "door_event" in self.include_events:
                senml_payload = self.create_door_event_senml_payload(
                    "door_opened", 
                    timestamp=self.door_open_start_time
                )
                
                topic = self.build_topic("door_event")
                if self.mqtt_client and self.connected and topic:
                    self.mqtt_client.myPublish(topic, senml_payload)
                    print(f"[EVENT] Door OPENED - SenML notification sent to {topic}")
            
            print("[SIM] Door opened automatically")
    
    def _simulate_door_close(self):
        """Simulate door closing event with immediate SenML notification"""
        if self.door_open:
            current_time = time.time()
            door_duration = current_time - self.door_open_start_time if self.door_open_start_time else 0
            self.door_open = False
            self.door_open_start_time = None
            
            # Send immediate door event in SenML format if configured
            if "door_event" in self.include_events:
                senml_payload = self.create_door_event_senml_payload(
                    "door_closed",
                    duration=door_duration,
                    timestamp=current_time
                )
                
                topic = self.build_topic("door_event")
                if self.mqtt_client and self.connected and topic:
                    self.mqtt_client.myPublish(topic, senml_payload)
                    print(f"[EVENT] Door CLOSED after {door_duration:.1f}s - SenML notification sent")
            
            print("[SIM] Door closed automatically")
    
    def publish_sensor_data(self):
        """Publish sensor data in SenML format to MQTT topics"""
        if not self.connected or not self.mqtt_client or not self.device_id:
            return
            
        current_time = time.time()
        
        for sensor_type, value in self.sensors.items():
            # Check if it's time to publish this sensor
            interval = self.sampling_intervals.get(sensor_type, 60)
            if current_time - self.last_publish[sensor_type] >= interval:
                topic = self.build_topic(sensor_type)
                if topic:
                    # Create SenML payload
                    senml_payload = self.create_senml_payload(sensor_type, value, current_time)
                    
                    # Publish using MyMQTT
                    self.mqtt_client.myPublish(topic, senml_payload)
                    self.last_publish[sensor_type] = current_time
                    
                    # Display published data with compressor state for temperature
                    unit = self._get_sensor_unit(sensor_type)
                    if sensor_type == "temperature":
                        comp_state = "ON" if self.compressor_on else "OFF"
                        print(f"[PUB] {sensor_type}: {value:.2f}{unit} (Comp: {comp_state}) -> {topic}")
                    else:
                        print(f"[PUB] {sensor_type}: {value:.2f}{unit} -> {topic}")
    
    def publish_heartbeat(self):
        """Publish heartbeat message in SenML format"""
        if not self.connected or not self.mqtt_client or not self.device_id:
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
                    "vs": "alive",  # String value
                    "t": 0
                }, {
                    "n": "uptime",
                    "v": current_time,
                    "u": "s",
                    "t": 0
                }]
            }
            
            self.mqtt_client.myPublish(heartbeat_topic, senml_payload)
            print(f"[HEARTBEAT] Published SenML to {heartbeat_topic}")
    
    def _get_sensor_unit(self, sensor_type):
        """Get unit for sensor type"""
        units = {
            "temperature": "Cel",  # SenML unit for Celsius
            "humidity": "%RH",     # SenML unit for relative humidity
            "light": "lx",         # SenML unit for lux
            "gas": "ppm"           # Parts per million
        }
        return units.get(sensor_type, "")
    
    def get_compressor_status(self):
        """Metodo per ottenere lo stato del compressore (utile per debugging)"""
        return {
            "compressor_on": self.compressor_on,
            "current_temp": self.sensors["temperature"],
            "target_temp": self.target_temperature,
            "last_change": self.compressor_last_change,
            "cycle_time_remaining": max(0, self.min_cycle_time - (time.time() - self.compressor_last_change))
        }
    
    def print_status(self):
        """Print current simulator status"""
        print("\n" + "=" * 50)
        print("    FRIDGE SIMULATOR STATUS")
        print("=" * 50)
        print(f"Device ID: {self.device_id}")
        print(f"Model: {self.model}")
        print(f"MAC Address: {self.mac_address}")
        print(f"MQTT Connected: {'Yes' if self.connected else 'No'}")
        print(f"Door State: {'OPEN' if self.door_open else 'CLOSED'}")
        print(f"Compressor State: {'ON' if self.compressor_on else 'OFF'}")
        print(f"Target Temperature: {self.target_temperature}°C")
        print(f"Spoilage Active: {'Yes' if self.spoilage_active else 'No'}")
        print(f"Malfunction Active: {'Yes' if self.malfunction_active else 'No'}")
        print(f"Automatic Mode: {'Yes' if self.automatic_mode else 'No'}")
        print(f"Data Format: SenML")
        print("\nCurrent Sensor Values:")
        for sensor, value in self.sensors.items():
            unit = self._get_sensor_unit(sensor)
            interval = self.sampling_intervals.get(sensor, 60)
            print(f"  {sensor.capitalize():12}: {value:6.2f} {unit:4} (every {interval}s)")
        
        # Info sui cicli del compressore
        if hasattr(self, 'compressor_last_change'):
            cycle_time = time.time() - self.compressor_last_change
            print(f"\nCompressor Cycle Info:")
            print(f"  Current cycle time: {cycle_time/60:.1f} minutes")
            print(f"  Min cycle time: {self.min_cycle_time/60:.1f} minutes")
            print(f"  Cooling rate: {self.cooling_rate}°C/hour")
            print(f"  Warming rate: {self.warming_rate}°C/hour")
        
        print("=" * 50 + "\n")
    
    def sensor_simulation_loop(self):
        """Main sensor simulation loop"""
        while self.running:
            try:
                self.generate_realistic_data()
                time.sleep(2)  # Update sensors every 2 seconds
            except Exception as e:
                print(f"[ERROR] Sensor simulation error: {e}")
    
    def mqtt_publish_loop(self):
        """MQTT publishing loop"""
        while self.running:
            try:
                self.publish_sensor_data()
                time.sleep(5)  # Check for publishing every 5 seconds
            except Exception as e:
                print(f"[ERROR] MQTT publishing error: {e}")
    
    def heartbeat_loop(self):
        """Heartbeat publishing loop"""
        while self.running:
            try:
                self.publish_heartbeat()
                time.sleep(self.heartbeat_interval)
            except Exception as e:
                print(f"[ERROR] Heartbeat error: {e}")
    
    def status_loop(self):
        """Periodic status reporting loop"""
        while self.running:
            try:
                time.sleep(300)  # Print status every 5 minutes
                self.print_status()
            except Exception as e:
                print(f"[ERROR] Status loop error: {e}")
    
    def run(self):
        """Main run method - start all processes"""
        print("=" * 60)
        print("    SMARTCHILL FRIDGE SIMULATOR (THERMAL MODEL)")
        print("=" * 60)
        print(f"MAC Address: {self.mac_address}")
        print(f"Model: {self.model}")
        print(f"Firmware: {self.firmware_version}")
        print(f"Data Format: SenML")
        print(f"Thermal Model: Compressor Duty Cycle")
        print(f"MQTT Commands: Enabled")
        print("=" * 60)
        
        # Step 1: Register with catalog
        print("\n[INIT] Step 1: Registering with catalog...")
        if not self.register_with_catalog():
            print("[INIT] Warning: Proceeding without catalog registration")
            # Use MAC-based device_id as fallback
            if not self.device_id:
                self.device_id = f"SmartChill_{self.mac_address.replace(':', '')[-6:]}"
            print(f"[INIT] Using fallback device ID: {self.device_id}")
        
        # Step 2: Setup MQTT connection
        print("\n[INIT] Step 2: Setting up MQTT connection...")
        if not self.setup_mqtt():
            print("[ERROR] Failed to setup MQTT connection")
            return
        
        print("\n[INIT] Simulator started successfully!")
        print("[INIT] Publishing sensor data in SenML format.")
        print("[INIT] Realistic thermal model with compressor cycles active.")
        print("[INIT] Automatic door operations enabled.")
        print("[INIT] MQTT command interface active.")
        print("\n[COMMANDS] Available MQTT commands:")
        print("[COMMANDS]   Topic: Group17/SmartChill/Commands/{device_id}/simulation")
        print("[COMMANDS]   Actions: spoilage_start, spoilage_stop, malfunction_start, malfunction_stop, reset, status")
        print("[COMMANDS]   Response: Group17/SmartChill/Response/{device_id}/command_result")
        
        # Print initial status
        self.print_status()
        
        # Start all threads
        threads = [
            threading.Thread(target=self.sensor_simulation_loop, daemon=True),
            threading.Thread(target=self.mqtt_publish_loop, daemon=True),
            threading.Thread(target=self.heartbeat_loop, daemon=True),
            threading.Thread(target=self.status_loop, daemon=True)
        ]
        
        for thread in threads:
            thread.start()
        
        # Main loop - keep program alive
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[SHUTDOWN] Received interrupt signal...")
            self.shutdown()
    
    def shutdown(self):
        """Graceful shutdown"""
        print("[SHUTDOWN] Stopping fridge simulator...")
        self.running = False
        
        if self.mqtt_client:
            try:
                self.mqtt_client.stop()
                print("[SHUTDOWN] MQTT connection closed")
            except Exception as e:
                print(f"[SHUTDOWN] Error closing MQTT: {e}")
        
        print("[SHUTDOWN] Fridge simulator stopped successfully")

def main():
    """Main entry point"""
    try:
        simulator = FridgeSimulator()
        simulator.run()
    except FileNotFoundError as e:
        print(f"[FATAL] {e}")
        print("[FATAL] Please ensure settings.json exists with proper configuration")
    except Exception as e:
        print(f"[FATAL] Simulator error: {e}")

if __name__ == "__main__":
    main()