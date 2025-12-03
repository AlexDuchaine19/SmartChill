import time
import random
from datetime import datetime

class FridgeSimulatorLogic:
    def __init__(self, settings, mqtt_client):
        self.settings = settings
        self.mqtt_client = mqtt_client
        
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

        # Simulation state
        self.door_open = False
        self.spoilage_active = False
        self.malfunction_active = False
        self.automatic_mode = True
        self.manual_door_control = False
        
        self.door_open_start_time = None
        
        # Thermal model parameters
        self.target_temperature = 4.0
        self.compressor_on = False
        self.last_temp_update = time.time()
        self.compressor_last_change = time.time()
        self.min_cycle_time = 60 # Minimum time between compressor state changes
        
        self.cooling_rate = 3.0
        self.warming_rate = 0.5
        self.door_warming_rate = 3.0
        
        self.temp_min = 3.5
        self.temp_max = 4.5
        
        # Initialize temperature
        self.sensors["temperature"] = self.target_temperature + random.uniform(-0.2, 0.2)

        # Publishing state
        self.last_publish = {sensor: 0 for sensor in self.sensors.keys()}

    def handle_simulation_command(self, command):
        """Handle simulation control commands"""
        try:
            action = command.get("action", "").lower()
            
            if action == "door_open":
                 self.manual_door_control = True
                 self._simulate_door_open()
                 self.mqtt_client.send_command_response("manual_door_on", True, "Manual door control ENABLED")
                 print("[CMD] Manual door control ENABLED")
            
            elif action == "door_close":
                 self.manual_door_control = False
                 self._simulate_door_close()
                 self.mqtt_client.send_command_response("manual_door_off", True, "Manual door control DISABLED")
                 print("[CMD] Manual door control DISABLED")

            elif action == "spoilage_start":
                self.spoilage_active = True
                self.mqtt_client.send_command_response("spoilage_start", True, "Spoilage simulation activated")
                print("[CMD] Spoilage simulation activated via MQTT")
                
            elif action == "spoilage_stop":
                self.spoilage_active = False
                self.mqtt_client.send_command_response("spoilage_stop", True, "Spoilage simulation deactivated")
                print("[CMD] Spoilage simulation deactivated via MQTT")
                
            elif action == "malfunction_start":
                self.malfunction_active = True
                self.mqtt_client.send_command_response("malfunction_start", True, "Malfunction simulation activated")
                print("[CMD] Malfunction simulation activated via MQTT")
                
            elif action == "malfunction_stop":
                self.malfunction_active = False
                self.mqtt_client.send_command_response("malfunction_stop", True, "Malfunction simulation deactivated")
                print("[CMD] Malfunction simulation deactivated via MQTT")
                
            elif action == "reset":
                self.spoilage_active = False
                self.malfunction_active = False
                if self.door_open:
                    self._simulate_door_close()
                self.sensors["temperature"] = self.target_temperature
                self.mqtt_client.send_command_response("reset", True, "Simulator reset to normal operation")
                print("[CMD] Simulator reset to normal operation via MQTT")
                
            elif action == "status":
                status = self.get_simulation_status()
                self.mqtt_client.send_command_response("status", True, "Status retrieved", status)
                
            else:
                self.mqtt_client.send_command_response("unknown", False, f"Unknown command: {action}")
                
        except Exception as e:
            print(f"[CMD] Error handling simulation command: {e}")
            self.mqtt_client.send_command_response("error", False, f"Command processing error: {str(e)}")

    def get_simulation_status(self):
        """Get current simulation status"""
        return {
            "door_open": self.door_open,
            "spoilage_active": self.spoilage_active,
            "malfunction_active": self.malfunction_active,
            "compressor_on": self.compressor_on,
            "target_temperature": self.target_temperature,
            "current_temperature": round(self.sensors["temperature"], 2),
            "automatic_mode": self.automatic_mode,
            "sensors": {k: round(v, 2) for k, v in self.sensors.items()},
            "uptime": time.time() - self.compressor_last_change # Approximate
        }

    def update_compressor_state(self):
        """Control compressor with hysteresis"""
        temp = self.sensors["temperature"]
        
        if not self.malfunction_active:
            if not self.compressor_on and temp >= self.temp_max:
                self.compressor_on = True
                self.compressor_last_change = time.time()
                print(f"[THERMAL] Compressor ON - Temp: {temp:.2f}°C (>= {self.temp_max}°C)")
                
            elif self.compressor_on and temp <= self.temp_min:
                self.compressor_on = False
                self.compressor_last_change = time.time()
                print(f"[THERMAL] Compressor OFF - Temp: {temp:.2f}°C (<= {self.temp_min}°C)")

    def simulate_thermal_dynamics(self):
        """Simulate thermal dynamics"""
        current_time = time.time()
        time_delta = current_time - self.last_temp_update
        self.last_temp_update = current_time
        
        dt_hours = time_delta / 3600.0
        current_temp = self.sensors["temperature"]
        
        if self.malfunction_active:
            temp_change = self.warming_rate * 3 * dt_hours
        elif self.door_open:
            temp_change = self.door_warming_rate * dt_hours
        elif self.compressor_on:
            temp_change = -self.cooling_rate * dt_hours
        else:
            temp_change = self.warming_rate * dt_hours
        
        new_temp = current_temp + temp_change
        
        if self.malfunction_active:
            new_temp = max(-5.0, min(25.0, new_temp))
        else:
            new_temp = max(0.0, min(10.0, new_temp))
        
        self.sensors["temperature"] = new_temp

    def _get_door_open_probability(self):
        now = datetime.now()
        current_hour = now.hour
        is_weekend = now.weekday() >= 5

        weekday_schedule = [(7, 9, 0.001), (12, 14, 0.0025), (19, 21, 0.0025)]
        weekend_schedule = [(9, 11, 0.0015), (13, 15, 0.003), (19, 22, 0.003)]
        schedule = weekend_schedule if is_weekend else weekday_schedule
        
        for start, end, prob in schedule:
            if start <= current_hour < end:
                return prob
        
        return 0.00001 if 0 <= current_hour < 6 else 0.0001

    def generate_realistic_data(self):
        """Generate realistic sensor data"""
        current_time = time.time()
        
        self.simulate_thermal_dynamics()
        self.update_compressor_state()
        
        # Humidity
        if self.door_open:
            self.sensors["humidity"] = min(90, self.sensors["humidity"] + random.uniform(0.1, 0.5))
        else:
            target_humidity = 65 - (self.sensors["temperature"] - 4) * 3
            diff = (target_humidity - self.sensors["humidity"]) * 0.05
            self.sensors["humidity"] += diff + random.uniform(-0.5, 0.5)
            self.sensors["humidity"] = max(30, min(85, self.sensors["humidity"]))
        
        # Light
        if self.door_open:
            self.sensors["light"] = random.uniform(120, 200)
        else:
            self.sensors["light"] = random.uniform(0, 8)
        
        # Gas
        if self.spoilage_active:
            self.sensors["gas"] = random.uniform(450, 700)
        else:
            self.sensors["gas"] = random.uniform(8, 45)
        
        # Automatic door events
        if (self.automatic_mode and not self.door_open and 
            random.random() < self._get_door_open_probability()):
            self._simulate_door_open()
        
        if (self.door_open and 
            not self.manual_door_control and
            self.door_open_start_time and 
            (current_time - self.door_open_start_time) > random.uniform(20, 120)):
            self._simulate_door_close()

    def _simulate_door_open(self):
        if not self.door_open:
            self.door_open = True
            self.door_open_start_time = time.time()
            self.mqtt_client.publish_door_event("door_opened", timestamp=self.door_open_start_time)
            print("[SIM] Door opened automatically")

    def _simulate_door_close(self):
        if self.door_open:
            current_time = time.time()
            door_duration = current_time - self.door_open_start_time if self.door_open_start_time else 0
            self.door_open = False
            self.door_open_start_time = None
            self.mqtt_client.publish_door_event("door_closed", duration=door_duration, timestamp=current_time)
            print("[SIM] Door closed automatically")

    def publish_sensor_data(self):
        """Publish sensor data if interval has passed"""
        current_time = time.time()
        sampling_intervals = self.settings.get("sampling_intervals", {})
        
        for sensor_type, value in self.sensors.items():
            interval = sampling_intervals.get(sensor_type, 60)
            if current_time - self.last_publish[sensor_type] >= interval:
                topic = self.mqtt_client.publish_sensor_data(sensor_type, value, current_time)
                if topic:
                    self.last_publish[sensor_type] = current_time
                    
                    if sensor_type == "temperature":
                        comp_state = "ON" if self.compressor_on else "OFF"
                        print(f"[PUB] {sensor_type}: {value:.2f} (Comp: {comp_state}) -> {topic}")
                    else:
                        print(f"[PUB] {sensor_type}: {value:.2f} -> {topic}")
