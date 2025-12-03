import time
from datetime import datetime, timezone

class SpoilageMonitor:
    def __init__(self, service):
        self.service = service
        self.last_alert_time = {}    # {device_id: timestamp} for cooldown tracking
        self.gas_status = {}         # {device_id: "normal"|"high"}

    def is_cooldown_active(self, device_id):
        """Check if device is in alert cooldown period"""
        if device_id not in self.last_alert_time:
            return False
        
        config = self.service.get_device_config(device_id)
        cooldown_minutes = config["alert_cooldown_minutes"]
        cooldown_seconds = cooldown_minutes * 60
        
        time_since_last_alert = time.time() - self.last_alert_time[device_id]
        return time_since_last_alert < cooldown_seconds

    def handle_gas_reading(self, device_id, gas_value, timestamp):
        """Handle incoming gas sensor reading"""
        config = self.service.get_device_config(device_id)
        threshold = config["gas_threshold_ppm"]
        enable_continuous = config["enable_continuous_alerts"]
        
        current_status = "high" if gas_value > threshold else "normal"
        previous_status = self.gas_status.get(device_id, "normal")
        
        # Update gas status
        self.gas_status[device_id] = current_status
        
        print(f"[GAS] {device_id}: {gas_value} PPM (threshold: {threshold} PPM) - Status: {current_status}")
        
        # Check if we should send an alert
        should_alert = False
        
        if current_status == "high":
            if enable_continuous:
                # Send alert if not in cooldown
                if not self.is_cooldown_active(device_id):
                    should_alert = True
            else:
                # Send alert only on transition from normal to high
                if previous_status == "normal":
                    should_alert = True
        
        if should_alert:
            self.send_spoilage_alert(device_id, gas_value, threshold, timestamp)

    def send_spoilage_alert(self, device_id, gas_value, threshold, timestamp):
        """Send food spoilage alert via MQTT"""
        config = self.service.get_device_config(device_id)
        
        alert_topic = f"Group17/SmartChill/{device_id}/Alerts/Spoilage"
        alert_payload = {
            "alert_type": "food_spoilage",
            "device_id": device_id,
            "message": f"High gas levels detected: {gas_value} PPM (threshold: {threshold} PPM). Possible food spoilage.",
            "gas_level_ppm": gas_value,
            "threshold_ppm": threshold,
            "over_threshold_by": gas_value - threshold,
            "severity": config.get("alert_severity", "warning"),
            "timestamp": timestamp.isoformat() if isinstance(timestamp, datetime) else timestamp,
            "service": self.service.service_id,
            "config_version": self.service.settings["configVersion"],
            "recommended_action": "Check fridge contents for spoiled food"
        }
        
        self.service.mqtt_client.publish(alert_topic, alert_payload)
        self.last_alert_time[device_id] = time.time()
        print(f"[ALERT] Spoilage alert sent for {device_id} - Gas: {gas_value} PPM > {threshold} PPM")
