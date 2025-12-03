import sys
import os
import time
import threading
from datetime import datetime, timezone

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from Common.BaseService import SmartChillBaseService

class TimerUsageControl(SmartChillBaseService):
    def __init__(self):
        super().__init__("settings.json", "TimerUsageControl")
        self.device_timers = {}
        self.alerted_devices = {}

    def get_default_device_config(self):
        return {
            "max_door_open_seconds": self.settings["defaults"]["max_door_open_seconds"],
            "check_interval": self.settings["defaults"]["check_interval"],
            "enable_door_closed_alerts": True
        }

    def validate_specific_config(self, config):
        if "max_door_open_seconds" in config:
            v = config["max_door_open_seconds"]
            if not isinstance(v, int) or v < 30: return "max_door_open_seconds invalid"
        return None

    def start_specific_tasks(self):
        t = threading.Thread(target=self.monitoring_loop, daemon=True)
        t.start()

    def process_sensor_data(self, topic, payload):
        events = self.parse_senml(payload)
        if not events: return
        
        topic_parts = topic.split('/')
        topic_dev_id = topic_parts[-2] if len(topic_parts) >= 2 else None

        for e in events:
            if e['n'] != "door_state": continue
            
            dev_id = e['device_id'] or topic_dev_id
            if dev_id not in self.known_devices:
                if not self.check_device_exists_in_catalog(dev_id): continue

            event_type = e['vs'] # door_opened / door_closed
            if event_type == "door_opened":
                self.device_timers[dev_id] = time.time()
                print(f"[TIMER] Started for {dev_id}")
            elif event_type == "door_closed":
                self.handle_door_closed(dev_id)

    def handle_door_closed(self, device_id):
        if device_id in self.device_timers:
            duration = time.time() - self.device_timers.pop(device_id)
            if device_id in self.alerted_devices:
                del self.alerted_devices[device_id]
                self.send_alert(device_id, "door_closed_late", duration)

    def monitoring_loop(self):
        while self.running:
            now = time.time()
            for dev_id, start_time in list(self.device_timers.items()):
                duration = now - start_time
                thresh = self.get_device_config(dev_id)["max_door_open_seconds"]
                
                if duration >= thresh and dev_id not in self.alerted_devices:
                    self.send_alert(dev_id, "door_timeout", duration)
                    self.alerted_devices[dev_id] = now
            time.sleep(1)

    def send_alert(self, device_id, type_, duration):
        topic = f"Group17/SmartChill/{device_id}/Alerts/Door"
        payload = {
            "alert_type": type_,
            "duration": duration,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        self.mqtt_client.myPublish(topic, payload)

if __name__ == "__main__":
    TimerUsageControl().run()