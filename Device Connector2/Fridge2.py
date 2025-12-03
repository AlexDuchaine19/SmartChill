import time
import threading
import json
from datetime import datetime, timezone

from modules.utils import load_settings, save_settings
from modules.catalog_client import CatalogClient
from modules.mqtt_client import MQTTClient
from modules.simulator import Simulator

SETTINGS_FILE = "settings.json"

class FridgeSimulator:
    def __init__(self):
        self.settings = load_settings(SETTINGS_FILE)
        
        # Device identity
        self.mac_address = self.settings["deviceInfo"]["mac_address"]
        self.model = self.settings["deviceInfo"]["model"]
        self.firmware_version = self.settings["deviceInfo"]["firmware_version"]
        self.device_id = self.settings["deviceInfo"].get("deviceID")
        
        # Configuration
        self.topic_template = self.settings["mqtt_data"]["topic_template"]
        self.include_events = self.settings["mqtt_data"].get("include_events", [])
        self.heartbeat_topic_template = self.settings["telemetry"]["heartbeat_topic"]
        self.heartbeat_interval = self.settings["telemetry"]["heartbeat_interval_s"]
        self.sampling_intervals = self.settings.get("sampling_intervals", {})
        
        # Initialize modules
        self.catalog_client = CatalogClient(self.settings)
        self.simulator = Simulator(self)
        self.mqtt_client = None # Initialized later
        
        # State
        self.running = True
        self.last_publish = {sensor: 0 for sensor in self.simulator.sensors.keys()}

    def register_with_catalog(self):
        """Register device with catalog"""
        config = self.catalog_client.register_device(
            self.mac_address, self.model, self.firmware_version, 
            self.settings["deviceInfo"]["sensors"]
        )
        
        if config:
            self.device_id = config["device_id"]
            self.settings["deviceInfo"]["deviceID"] = self.device_id
            save_settings(self.settings, SETTINGS_FILE)
            return True
        
        # Fallback
        if not self.device_id:
            self.device_id = f"SmartChill_{self.mac_address.replace(':', '')}"
        return False

    def setup_mqtt(self):
        """Setup MQTT connection"""
        self.mqtt_client = MQTTClient(self.settings, self.device_id, self)
        return self.mqtt_client.start()

    def build_topic(self, sensor_or_event):
        if not self.device_id: return None
        return self.topic_template.format(
            model=self.model, device_id=self.device_id, sensor=sensor_or_event
        )

    def build_heartbeat_topic(self):
        if not self.device_id: return None
        return self.heartbeat_topic_template.format(
            model=self.model, device_id=self.device_id
        )

    def build_command_topic(self, command_type):
        if not self.device_id: return None
        return f"Group17/SmartChill/Commands/{self.device_id}/{command_type}"

    def build_response_topic(self):
        if not self.device_id: return None
        return f"Group17/SmartChill/Response/{self.device_id}/command_result"

    def create_senml_payload(self, sensor_type, value, timestamp=None):
        if timestamp is None: timestamp = time.time()
        base_name = f"{self.device_id}/"
        unit = self._get_sensor_unit(sensor_type)
        
        return {
            "bn": base_name, "bt": timestamp,
            "e": [{"n": sensor_type, "v": round(value, 2), "u": unit, "t": 0}]
        }

    def send_door_event(self, event_type, duration=None, timestamp=None):
        if timestamp is None: timestamp = time.time()
        base_name = f"{self.device_id}/"
        
        entries = [{"n": "door_state", "vs": event_type, "t": 0}]
        if event_type == "door_closed" and duration is not None:
            entries.append({"n": "door_duration", "v": round(duration, 1), "u": "s", "t": 0})
            
        senml_payload = {"bn": base_name, "bt": timestamp, "e": entries}
        
        topic = self.build_topic("door_event")
        if self.mqtt_client and self.mqtt_client.connected and topic:
            self.mqtt_client.publish(topic, senml_payload)
            print(f"[EVENT] {event_type} - SenML notification sent")

    def handle_message(self, topic, payload):
        try:
            message = json.loads(payload)
            if "update_config" in topic:
                self._handle_config_update(message)
            elif "simulation" in topic:
                self._handle_simulation_command(message)
        except Exception as e:
            print(f"[MQTT] Error processing message: {e}")

    def _handle_config_update(self, message):
        print(f"[MQTT] Received config update: {message}")
        if "sampling_intervals" in message:
            self.sampling_intervals.update(message["sampling_intervals"])
            self.settings["sampling_intervals"] = self.sampling_intervals
            save_settings(self.settings, SETTINGS_FILE)

    def _handle_simulation_command(self, command):
        try:
            action = command.get("action", "").lower()
            sim = self.simulator
            
            if action == "door_open":
                sim.manual_door_control = True
                sim._simulate_door_open()
                self._send_command_response("manual_door_on", True, "Manual door control ENABLED")
            elif action == "door_close":
                sim.manual_door_control = False
                sim._simulate_door_close()
                self._send_command_response("manual_door_off", True, "Manual door control DISABLED")
            elif action == "spoilage_start":
                sim.spoilage_active = True
                self._send_command_response("spoilage_start", True, "Spoilage simulation activated")
            elif action == "spoilage_stop":
                sim.spoilage_active = False
                self._send_command_response("spoilage_stop", True, "Spoilage simulation deactivated")
            elif action == "malfunction_start":
                sim.malfunction_active = True
                self._send_command_response("malfunction_start", True, "Malfunction simulation activated")
            elif action == "malfunction_stop":
                sim.malfunction_active = False
                self._send_command_response("malfunction_stop", True, "Malfunction simulation deactivated")
            elif action == "reset":
                sim.spoilage_active = False
                sim.malfunction_active = False
                if sim.door_open: sim._simulate_door_close()
                sim.sensors["temperature"] = sim.target_temperature
                self._send_command_response("reset", True, "Simulator reset")
            elif action == "status":
                status = self.get_simulation_status()
                self._send_command_response("status", True, "Status retrieved", status)
            else:
                self._send_command_response("unknown", False, f"Unknown command: {action}")
                
        except Exception as e:
            print(f"[CMD] Error: {e}")
            self._send_command_response("error", False, str(e))

    def _send_command_response(self, command, success, message, data=None):
        response_topic = self.build_response_topic()
        if response_topic and self.mqtt_client and self.mqtt_client.connected:
            response = {
                "command": command, "success": success, "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(), "device_id": self.device_id
            }
            if data: response["data"] = data
            self.mqtt_client.publish(response_topic, response)

    def get_simulation_status(self):
        sim = self.simulator
        return {
            "device_id": self.device_id,
            "door_open": sim.door_open,
            "spoilage_active": sim.spoilage_active,
            "malfunction_active": sim.malfunction_active,
            "compressor_on": sim.compressor_on,
            "sensors": {k: round(v, 2) for k, v in sim.sensors.items()}
        }

    def _get_sensor_unit(self, sensor_type):
        return {"temperature": "Cel", "humidity": "%RH", "light": "lx", "gas": "ppm"}.get(sensor_type, "")

    def publish_sensor_data(self):
        if not self.mqtt_client or not self.mqtt_client.connected: return
        current_time = time.time()
        
        for sensor_type, value in self.simulator.sensors.items():
            interval = self.sampling_intervals.get(sensor_type, 60)
            if current_time - self.last_publish[sensor_type] >= interval:
                topic = self.build_topic(sensor_type)
                if topic:
                    payload = self.create_senml_payload(sensor_type, value, current_time)
                    self.mqtt_client.publish(topic, payload)
                    self.last_publish[sensor_type] = current_time
                    print(f"[PUB] {sensor_type}: {value:.2f} -> {topic}")

    def publish_heartbeat(self):
        if not self.mqtt_client or not self.mqtt_client.connected: return
        topic = self.build_heartbeat_topic()
        if topic:
            payload = {
                "bn": f"{self.device_id}/", "bt": time.time(),
                "e": [{"n": "heartbeat", "vs": "alive", "t": 0}]
            }
            self.mqtt_client.publish(topic, payload)

    def run(self):
        print("="*60 + "\n    SMARTCHILL FRIDGE SIMULATOR 2 (MODULAR)\n" + "="*60)
        
        if not self.register_with_catalog():
            print("[WARN] Proceeding without catalog registration")
        
        if not self.setup_mqtt():
            print("[ERROR] Failed to setup MQTT")
            return
            
        print("[INIT] Simulator started successfully!")
        
        threads = [
            threading.Thread(target=self._sensor_loop, daemon=True),
            threading.Thread(target=self._publish_loop, daemon=True),
            threading.Thread(target=self._heartbeat_loop, daemon=True),
            threading.Thread(target=self._status_loop, daemon=True)
        ]
        for t in threads: t.start()
        
        try:
            while self.running: time.sleep(1)
        except KeyboardInterrupt:
            self.shutdown()

    def _sensor_loop(self):
        while self.running:
            self.simulator.generate_realistic_data()
            time.sleep(2)

    def _publish_loop(self):
        while self.running:
            self.publish_sensor_data()
            time.sleep(5)

    def _heartbeat_loop(self):
        while self.running:
            self.publish_heartbeat()
            time.sleep(self.heartbeat_interval)

    def _status_loop(self):
        while self.running:
            time.sleep(300)
            print(f"\n[STATUS] {self.get_simulation_status()}")

    def shutdown(self):
        print("[SHUTDOWN] Stopping simulator...")
        self.running = False
        if self.mqtt_client: self.mqtt_client.stop()

if __name__ == "__main__":
    try:
        FridgeSimulator().run()
    except Exception as e:
        print(f"[FATAL] {e}")