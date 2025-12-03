import time
from datetime import datetime, timezone

class TimerManager:
    def __init__(self, service):
        self.service = service
        self.device_timers = {}      # {device_id: start_time}
        self.alerted_devices = {}    # {device_id: alert_sent_time}

    def handle_door_opened(self, device_id):
        """Handle door opened event - start timer"""
        current_time = time.time()
        self.device_timers[device_id] = current_time
        print(f"[TIMER] Door OPENED for {device_id} - timer started")

    def handle_door_closed(self, device_id):
        """Handle door closed event - stop timer and send alert if needed"""
        if device_id in self.device_timers:
            start_time = self.device_timers[device_id]
            duration = time.time() - start_time
            del self.device_timers[device_id]
            
            config = self.service.get_device_config(device_id)
            
            # Check if this device had an active alert and door closed alerts are enabled
            if (device_id in self.alerted_devices and 
                config.get("enable_door_closed_alerts", True)):
                self.send_door_closed_alert(device_id, duration)
                del self.alerted_devices[device_id]
                print(f"[TIMER] Door CLOSED for {device_id} after {duration:.1f}s - ALERT SENT")
            else:
                print(f"[TIMER] Door CLOSED for {device_id} after {duration:.1f}s - no alert needed")
        else:
            print(f"[TIMER] Door CLOSED for {device_id} but no active timer found")

    def check_timeouts(self):
        """Check for doors that have been open too long and send alerts"""
        current_time = time.time()
        
        for device_id, start_time in list(self.device_timers.items()):
            duration = current_time - start_time
            config = self.service.get_device_config(device_id)
            threshold = config["max_door_open_seconds"]
            
            # Check if threshold exceeded and alert not yet sent
            if duration >= threshold and device_id not in self.alerted_devices:
                self.send_door_timeout_alert(device_id, duration)
                self.alerted_devices[device_id] = current_time
                print(f"[TIMEOUT] Door timeout alert triggered for {device_id} - {duration:.0f}s > {threshold}s")

    def send_door_timeout_alert(self, device_id, duration):
        """Send door timeout alert via MQTT"""
        config = self.service.get_device_config(device_id)
        threshold = config["max_door_open_seconds"]
        
        alert_topic = f"Group17/SmartChill/{device_id}/Alerts/DoorTimeout"
        alert_payload = {
            "alert_type": "door_timeout",
            "device_id": device_id,
            "message": f"Door has been open for {duration:.0f} seconds (threshold: {threshold}s)",
            "duration_seconds": round(duration, 1),
            "threshold_seconds": threshold,
            "severity": "warning",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": self.service.service_id,
            "config_version": self.service.settings["configVersion"]
        }
        
        self.service.mqtt_client.publish(alert_topic, alert_payload)
        print(f"[ALERT] Door timeout alert sent for {device_id}")

    def send_door_closed_alert(self, device_id, total_duration):
        """Send door closed alert via MQTT"""
        config = self.service.get_device_config(device_id)
        threshold = config["max_door_open_seconds"]
        
        alert_topic = f"Group17/SmartChill/{device_id}/Alerts/DoorClosed"
        alert_payload = {
            "alert_type": "door_closed_after_timeout",
            "device_id": device_id,
            "message": f"Door closed after {total_duration:.0f} seconds (was over {threshold}s threshold)",
            "total_duration_seconds": round(total_duration, 1),
            "threshold_seconds": threshold,
            "over_threshold_by": round(total_duration - threshold, 1),
            "severity": "info",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": self.service.service_id,
            "config_version": self.service.settings["configVersion"]
        }
        
        self.service.mqtt_client.publish(alert_topic, alert_payload)
        print(f"[ALERT] Door closed alert sent for {device_id}")
