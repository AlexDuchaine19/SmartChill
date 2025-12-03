import time
from datetime import datetime, timezone

class StatusMonitor:
    def __init__(self, service):
        self.service = service
        self.last_alert_time = {}           # {device_id: {alert_type: timestamp}}
        self.device_status = {}             # {device_id: {"temp_status": ..., "humidity_status": ...}}
        self.last_readings = {}             # {device_id: {"temperature": val, "humidity": val, "timestamp": ts}}

    def is_cooldown_active(self, device_id, alert_type):
        """Check if device is in alert cooldown period for specific alert type"""
        if device_id not in self.last_alert_time or alert_type not in self.last_alert_time[device_id]:
            return False
        
        config = self.service.get_device_config(device_id)
        cooldown_minutes = config["alert_cooldown_minutes"]
        cooldown_seconds = cooldown_minutes * 60
        
        time_since_last_alert = time.time() - self.last_alert_time[device_id][alert_type]
        return time_since_last_alert < cooldown_seconds

    def analyze_temperature_status(self, device_id, temperature, timestamp):
        """Analyze temperature reading and detect anomalies"""
        config = self.service.get_device_config(device_id)
        temp_min = config["temp_min_celsius"]
        temp_max = config["temp_max_celsius"]
        
        if temperature < temp_min:
            status = "too_low"
            alert_type = "temperature_too_low"
            message = f"Temperature too low: {temperature:.1f}°C (min: {temp_min}°C). Risk of freezing food items."
            recommended_action = "Check thermostat settings and increase temperature"
            severity = "warning"
        elif temperature > temp_max:
            status = "too_high"
            alert_type = "temperature_too_high"
            message = f"Temperature too high: {temperature:.1f}°C (max: {temp_max}°C). Risk of food spoilage."
            recommended_action = "Check thermostat settings, door seals, and reduce temperature"
            severity = "critical"
        else:
            status = "normal"
            alert_type = None
            message = None
            recommended_action = None
            severity = None
        
        if device_id not in self.device_status:
            self.device_status[device_id] = {}
        
        previous_temp_status = self.device_status[device_id].get("temp_status", "normal")
        self.device_status[device_id]["temp_status"] = status
        
        print(f"[TEMP] {device_id}: {temperature:.1f}°C (range: {temp_min}-{temp_max}°C) - Status: {status}")
        
        if alert_type and config["enable_malfunction_alerts"]:
            if previous_temp_status == "normal" or not self.is_cooldown_active(device_id, alert_type):
                self.send_malfunction_alert(device_id, alert_type, message, temperature, severity, recommended_action, timestamp)

    def analyze_humidity_status(self, device_id, humidity, timestamp):
        """Analyze humidity reading and detect anomalies"""
        config = self.service.get_device_config(device_id)
        humidity_max = config["humidity_max_percent"]
        
        if humidity > humidity_max:
            status = "too_high"
            alert_type = "humidity_too_high"
            message = f"Humidity too high: {humidity:.1f}% (max: {humidity_max}%). Risk of ice formation and condensation."
            recommended_action = "Check door seals, defrost if needed, ensure proper air circulation"
            severity = "warning"
        else:
            status = "normal"
            alert_type = None
            message = None
            recommended_action = None
            severity = None
        
        if device_id not in self.device_status:
            self.device_status[device_id] = {}
        
        previous_humidity_status = self.device_status[device_id].get("humidity_status", "normal")
        self.device_status[device_id]["humidity_status"] = status
        
        print(f"[HUMIDITY] {device_id}: {humidity:.1f}% (max: {humidity_max}%) - Status: {status}")
        
        if alert_type and config["enable_malfunction_alerts"]:
            if previous_humidity_status == "normal" or not self.is_cooldown_active(device_id, alert_type):
                self.send_malfunction_alert(device_id, alert_type, message, humidity, severity, recommended_action, timestamp)

    def detect_malfunction_patterns(self, device_id):
        """Detect complex malfunction patterns based on combined sensor data"""
        if device_id not in self.last_readings:
            return
        
        readings = self.last_readings[device_id]
        temperature = readings.get("temperature")
        humidity = readings.get("humidity")
        
        if temperature is None or humidity is None:
            return
        
        config = self.service.get_device_config(device_id)
        
        if temperature > config["temp_max_celsius"] and humidity > config["humidity_max_percent"]:
            alert_type = "cooling_system_failure"
            message = f"Possible cooling system failure: High temperature ({temperature:.1f}°C) and humidity ({humidity:.1f}%)"
            recommended_action = "Check cooling system, compressor, and refrigerant levels. Contact technician if needed."
            severity = "critical"
            
            if not self.is_cooldown_active(device_id, alert_type):
                self.send_malfunction_alert(device_id, alert_type, message, 
                                          {"temperature": temperature, "humidity": humidity}, 
                                          severity, recommended_action, readings["timestamp"])
        
        elif temperature < config["temp_min_celsius"] and humidity > config["humidity_max_percent"]:
            alert_type = "defrost_cycle_issue"
            message = f"Possible defrost cycle issue: Low temperature ({temperature:.1f}°C) with high humidity ({humidity:.1f}%)"
            recommended_action = "Check defrost cycle settings and drainage system"
            severity = "warning"
            
            if not self.is_cooldown_active(device_id, alert_type):
                self.send_malfunction_alert(device_id, alert_type, message, 
                                          {"temperature": temperature, "humidity": humidity}, 
                                          severity, recommended_action, readings["timestamp"])

    def send_malfunction_alert(self, device_id, alert_type, message, sensor_value, severity, recommended_action, timestamp):
        """Send malfunction alert via MQTT"""
        alert_topic = f"Group17/SmartChill/{device_id}/Alerts/Malfunction"
        alert_payload = {
            "alert_type": alert_type,
            "device_id": device_id,
            "message": message,
            "sensor_values": sensor_value if isinstance(sensor_value, dict) else {alert_type.split('_')[0]: sensor_value},
            "severity": severity,
            "timestamp": timestamp.isoformat() if isinstance(timestamp, datetime) else timestamp,
            "service": self.service.service_id,
            "config_version": self.service.settings["configVersion"],
            "recommended_action": recommended_action
        }
        
        self.service.mqtt_client.publish(alert_topic, alert_payload)
        
        if device_id not in self.last_alert_time:
            self.last_alert_time[device_id] = {}
        self.last_alert_time[device_id][alert_type] = time.time()
        
        print(f"[ALERT] Malfunction alert sent for {device_id}: {alert_type}")
