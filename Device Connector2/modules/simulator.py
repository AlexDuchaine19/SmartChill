import time
import random
from datetime import datetime

class Simulator:
    def __init__(self, service):
        self.service = service
        self.sensors = {}
        for sensor in service.settings["deviceInfo"]["sensors"]:
            if sensor == "temperature": self.sensors[sensor] = 4.0
            elif sensor == "humidity": self.sensors[sensor] = 55.0
            elif sensor == "gas": self.sensors[sensor] = 20.0
            elif sensor == "light": self.sensors[sensor] = 5.0
            
        # Thermal model parameters
        self.target_temperature = 4.0
        self.compressor_on = False
        self.last_temp_update = time.time()
        self.cooling_rate = 3.0
        self.warming_rate = 0.5
        self.door_warming_rate = 3.0
        self.temp_min = 3.5
        self.temp_max = 4.5
        
        # Initialize temperature
        self.sensors["temperature"] = self.target_temperature + random.uniform(-0.2, 0.2)
        
        # State
        self.door_open = False
        self.spoilage_active = False
        self.malfunction_active = False
        self.automatic_mode = True
        self.manual_door_control = False
        self.door_open_start_time = None

    def update_compressor_state(self):
        """Control compressor with hysteresis"""
        temp = self.sensors["temperature"]
        if not self.malfunction_active:
            if not self.compressor_on and temp >= self.temp_max:
                self.compressor_on = True
                print(f"[THERMAL] Compressor ON - Temp: {temp:.2f}°C (>= {self.temp_max}°C)")
            elif self.compressor_on and temp <= self.temp_min:
                self.compressor_on = False
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
        """Calculate door open probability based on time of day"""
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
        self.sensors["light"] = random.uniform(120, 200) if self.door_open else random.uniform(0, 8)
        
        # Gas
        self.sensors["gas"] = random.uniform(450, 700) if self.spoilage_active else random.uniform(8, 45)
        
        # Door events
        if (self.automatic_mode and not self.door_open and 
            random.random() < self._get_door_open_probability()):
            self._simulate_door_open()
        
        if (self.door_open and not self.manual_door_control and 
            self.door_open_start_time and 
            (current_time - self.door_open_start_time) > random.uniform(20, 120)):
            self._simulate_door_close()

    def _simulate_door_open(self):
        if not self.door_open:
            self.door_open = True
            self.door_open_start_time = time.time()
            if "door_event" in self.service.include_events:
                self.service.send_door_event("door_opened", timestamp=self.door_open_start_time)
            print("[SIM] Door opened automatically")

    def _simulate_door_close(self):
        if self.door_open:
            current_time = time.time()
            duration = current_time - self.door_open_start_time if self.door_open_start_time else 0
            self.door_open = False
            self.door_open_start_time = None
            if "door_event" in self.service.include_events:
                self.service.send_door_event("door_closed", duration=duration, timestamp=current_time)
            print("[SIM] Door closed automatically")
