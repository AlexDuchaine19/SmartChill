import sys
import os
import time
import threading
from datetime import datetime, timezone

# Aggiungi Common al path (necessario per importare BaseService)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from Common.BaseService import SmartChillBaseService

class FoodSpoilageControl(SmartChillBaseService):
    def __init__(self):
        super().__init__("settings.json", "FoodSpoilageControl")
        self.last_alert_time = {}
        self.gas_status = {}

    def get_default_device_config(self):
        return {
            "gas_threshold_ppm": self.settings["defaults"]["gas_threshold_ppm"],
            "enable_continuous_alerts": self.settings["defaults"]["enable_continuous_alerts"],
            "alert_cooldown_minutes": self.settings["defaults"]["alert_cooldown_minutes"]
        }

    def validate_specific_config(self, config):
        # Logica di validazione specifica
        if "gas_threshold_ppm" in config:
            v = config["gas_threshold_ppm"]
            if not isinstance(v, int) or v < 100 or v > 1000: return "gas_threshold_ppm invalid"
        # ... altri controlli ...
        return None

    def start_specific_tasks(self):
        # Avvia thread di monitoraggio status
        t = threading.Thread(target=self.status_monitor_loop, daemon=True)
        t.start()

    def process_sensor_data(self, topic, payload):
        events = self.parse_senml(payload)
        if not events: return
        
        topic_parts = topic.split('/')
        topic_dev_id = topic_parts[-2] if len(topic_parts) >= 2 else None

        for e in events:
            if e['n'] != "gas": continue
            
            dev_id = e['device_id'] or topic_dev_id
            
            # Check esistenza
            if dev_id not in self.known_devices:
                if not self.check_device_exists_in_catalog(dev_id): continue

            val = e['v']
            ts = datetime.fromtimestamp(e['t'], tz=timezone.utc)
            self.handle_gas_reading(dev_id, float(val), ts)

    def handle_gas_reading(self, device_id, gas_value, timestamp):
        config = self.get_device_config(device_id)
        threshold = config["gas_threshold_ppm"]
        status = "high" if gas_value > threshold else "normal"
        prev_status = self.gas_status.get(device_id, "normal")
        self.gas_status[device_id] = status

        should_alert = False
        if status == "high":
            if config["enable_continuous_alerts"]:
                if not self.is_cooldown_active(device_id): should_alert = True
            elif prev_status == "normal":
                should_alert = True
        
        if should_alert:
            self.send_alert(device_id, gas_value, threshold, timestamp)

    def is_cooldown_active(self, device_id):
        if device_id not in self.last_alert_time: return False
        mins = self.get_device_config(device_id)["alert_cooldown_minutes"]
        return (time.time() - self.last_alert_time[device_id]) < (mins * 60)

    def send_alert(self, device_id, val, thresh, ts):
        topic = f"Group17/SmartChill/{device_id}/Alerts/Spoilage"
        payload = {
            "alert_type": "food_spoilage",
            "message": f"Gas level {val} > {thresh}",
            "value": val,
            "timestamp": ts.isoformat()
        }
        self.mqtt_client.myPublish(topic, payload)
        self.last_alert_time[device_id] = time.time()
        print(f"[ALERT] Sent for {device_id}")

    def status_monitor_loop(self):
        while self.running:
            # Logica di print status periodica
            time.sleep(30)

if __name__ == "__main__":
    FoodSpoilageControl().run()