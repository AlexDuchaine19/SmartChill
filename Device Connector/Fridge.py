import requests
import json
import time
import threading
import random
import math
from datetime import datetime, timezone
from queue import Queue
from MyMQTT import MyMQTT
import os

SETTINGS_FILE = r"C:\Users\Luca\Desktop\Programmi\SmartChill2\Device Connector\settings.json"

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
        self.manual_override = False
        
        # Threading
        self.command_queue = Queue()
        
        # Realistic simulation parameters
        self.compressor_cycle = 0
        self.base_temperature = 4.0
        self.door_open_start_time = None
        
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
            self.device_id = f"SmartChill_{self.mac_address.replace(':', '')[-6:]}"
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
            
            # Subscribe to command topic
            command_topic = f"Group17/SmartChill/Commands/{self.device_id}/update_config"
            self.mqtt_client.mySubscribe(command_topic)
            print(f"[MQTT] Subscribed to: {command_topic}")
            
            print("[MQTT] Connected successfully")
            return True
                
        except Exception as e:
            print(f"[MQTT] Connection error: {e}")
            return False
    
    def notify(self, topic, payload):
        """Callback method for MyMQTT - handles incoming messages"""
        try:
            message = json.loads(payload)
            print(f"[MQTT] Received config update: {message}")
            
            # Update sampling intervals if provided
            if "sampling_intervals" in message:
                old_intervals = self.sampling_intervals.copy()
                self.sampling_intervals.update(message["sampling_intervals"])
                
                print(f"[CONFIG] Updated sampling intervals:")
                for sensor in message["sampling_intervals"]:
                    old_val = old_intervals.get(sensor, "unknown")
                    new_val = self.sampling_intervals[sensor]
                    print(f"[CONFIG]   {sensor}: {old_val}s -> {new_val}s")
                
                # Save updated settings
                self._save_settings()
                
        except Exception as e:
            print(f"[MQTT] Error processing message: {e}")
    
    def generate_realistic_data(self):
        """Generate realistic sensor data based on current state"""
        current_time = time.time()
        
        # Temperature simulation with compressor cycles
        if not self.malfunction_active:
            if self.door_open:
                # Door open - temperature rises gradually
                if self.door_open_start_time:
                    door_open_duration = current_time - self.door_open_start_time
                    temp_rise = min(door_open_duration / 180.0 * 3, 4)  # Max 4°C rise after 3 minutes
                    self.sensors["temperature"] = self.base_temperature + temp_rise
            else:
                # Normal operation with compressor cycles
                self.compressor_cycle += 0.05
                temp_variation = 1.0 * math.sin(self.compressor_cycle) + random.uniform(-0.2, 0.2)
                self.sensors["temperature"] = max(1, min(7, self.base_temperature + temp_variation))
        else:
            # Malfunction - temperature increases uncontrollably
            self.sensors["temperature"] += random.uniform(0.1, 0.5)
            self.sensors["temperature"] = min(15, self.sensors["temperature"])
        
        # Humidity correlated with temperature and door state
        if self.door_open:
            # Door open - humidity increases due to ambient air
            self.sensors["humidity"] = min(90, self.sensors["humidity"] + random.uniform(0.1, 0.5))
        else:
            # Normal operation - humidity inversely correlated with temperature
            target_humidity = 65 - (self.sensors["temperature"] - 4) * 3
            diff = (target_humidity - self.sensors["humidity"]) * 0.05
            self.sensors["humidity"] += diff + random.uniform(-0.5, 0.5)
            self.sensors["humidity"] = max(30, min(85, self.sensors["humidity"]))
        
        # Light sensor - clear distinction between door states
        if self.door_open:
            self.sensors["light"] = random.uniform(120, 200)
        else:
            self.sensors["light"] = random.uniform(0, 8)
        
        # Gas sensor - spoilage detection
        if self.spoilage_active:
            self.sensors["gas"] = random.uniform(450, 700)
        else:
            self.sensors["gas"] = random.uniform(8, 45)
        
        # Automatic door events (realistic frequency)
        if (self.automatic_mode and not self.door_open and 
            random.random() < 0.0003):  # ~1 door opening every 55 minutes on average
            self._simulate_door_open()
        
        # Automatic door closing after realistic duration
        if (self.door_open and not self.manual_override and self.door_open_start_time and 
            (current_time - self.door_open_start_time) > random.uniform(20, 90)):
            self._simulate_door_close()
    
    def _simulate_door_open(self, manual=False):
        """Simulate door opening event with immediate SenML notification"""
        if not self.door_open:
            self.door_open = True
            self.door_open_start_time = time.time()
            self.manual_override = manual
            
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
            
            print("[SIM] Door opened")
    
    def _simulate_door_close(self, manual=False):
        """Simulate door closing event with immediate SenML notification"""
        if self.door_open:
            current_time = time.time()
            door_duration = current_time - self.door_open_start_time if self.door_open_start_time else 0
            self.door_open = False
            self.door_open_start_time = None
            self.manual_override = False
            
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
            
            print("[SIM] Door closed")
    
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
                    
                    # Display published data
                    unit = self._get_sensor_unit(sensor_type)
                    print(f"[PUB] {sensor_type}: {value:.2f}{unit} -> {topic} (SenML)")
    
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
    
    def handle_user_commands(self):
        """Handle user input commands (non-blocking)"""
        while self.running:
            try:
                command = input().strip().lower()
                if command:
                    self.command_queue.put(command)
            except (EOFError, KeyboardInterrupt):
                self.command_queue.put("quit")
                break
    
    def process_user_commands(self):
        """Process queued user commands"""
        while self.running:
            try:
                if not self.command_queue.empty():
                    command = self.command_queue.get_nowait()
                    self._execute_command(command)
                time.sleep(0.1)
            except:
                pass
    
    def _execute_command(self, command):
        """Execute user command"""
        if command == "apri":
            self._simulate_door_open(manual=True)
            print("[CMD] Door opened manually")
        elif command == "chiudi":
            self._simulate_door_close(manual=True)
            print("[CMD] Door closed manually")
        elif command == "spoilage":
            self.spoilage_active = True
            print("[CMD] Spoilage simulation activated")
        elif command == "malfunzione":
            self.malfunction_active = True
            print("[CMD] Malfunction simulation activated")
        elif command == "normale":
            self.spoilage_active = False
            self.malfunction_active = False
            if self.door_open:
                self._simulate_door_close(manual=True)
            self.automatic_mode = True
            self.sensors["temperature"] = self.base_temperature
            print("[CMD] Returned to normal operation")
        elif command == "status":
            self._print_status()
        elif command == "help":
            self._print_help()
        elif command in ["quit", "exit"]:
            print("[CMD] Shutting down simulator...")
            self.shutdown()
        else:
            print(f"[CMD] Unknown command: '{command}' - type 'help' for available commands")
    
    def _print_status(self):
        """Print current simulator status"""
        print("\n" + "=" * 40)
        print("    FRIDGE SIMULATOR STATUS")
        print("=" * 40)
        print(f"Device ID: {self.device_id}")
        print(f"Model: {self.model}")
        print(f"MAC Address: {self.mac_address}")
        print(f"MQTT Connected: {'Yes' if self.connected else 'No'}")
        print(f"Door State: {'OPEN' if self.door_open else 'CLOSED'}")
        print(f"Spoilage Active: {'Yes' if self.spoilage_active else 'No'}")
        print(f"Malfunction Active: {'Yes' if self.malfunction_active else 'No'}")
        print(f"Automatic Mode: {'Yes' if self.automatic_mode else 'No'}")
        print(f"Data Format: SenML")
        print("\nCurrent Sensor Values:")
        for sensor, value in self.sensors.items():
            unit = self._get_sensor_unit(sensor)
            interval = self.sampling_intervals.get(sensor, 60)
            print(f"  {sensor.capitalize():12}: {value:6.2f} {unit:4} (every {interval}s)")
        print("=" * 40 + "\n")
    
    def _print_help(self):
        """Print available commands"""
        print("\n" + "-" * 30)
        print("  AVAILABLE COMMANDS")
        print("-" * 30)
        print("  apri        - Open fridge door")
        print("  chiudi      - Close fridge door") 
        print("  spoilage    - Activate food spoilage simulation")
        print("  malfunzione - Activate malfunction simulation")
        print("  normale     - Return to normal operation")
        print("  status      - Show current status")
        print("  help        - Show this help")
        print("  quit/exit   - Shutdown simulator")
        print("-" * 30 + "\n")
    
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
    
    def run(self):
        """Main run method - start all processes"""
        print("=" * 50)
        print("    SMARTCHILL FRIDGE SIMULATOR (SenML)")
        print("=" * 50)
        print(f"MAC Address: {self.mac_address}")
        print(f"Model: {self.model}")
        print(f"Firmware: {self.firmware_version}")
        print(f"Data Format: SenML")
        print("=" * 50)
        
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
        print("[INIT] Automatic realistic patterns are active.")
        self._print_help()
        
        # Start all threads
        threads = [
            threading.Thread(target=self.handle_user_commands, daemon=True),
            threading.Thread(target=self.process_user_commands, daemon=True),
            threading.Thread(target=self.sensor_simulation_loop, daemon=True),
            threading.Thread(target=self.mqtt_publish_loop, daemon=True),
            threading.Thread(target=self.heartbeat_loop, daemon=True)
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