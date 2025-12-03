import time
import threading
from modules.utils import load_settings
from modules.catalog_client import CatalogClient
from modules.mqtt_client import MQTTClient
from modules.simulator import FridgeSimulatorLogic

class FridgeSimulator:
    def __init__(self):
        # Load settings
        self.settings = load_settings()
        
        # Initialize components
        self.catalog_client = CatalogClient(self.settings)
        self.simulator = None # Will be initialized after MQTT
        self.mqtt_client = None
        
        # State
        self.running = True
        self.device_id = self.settings["deviceInfo"].get("deviceID")
        self.mac_address = self.settings["deviceInfo"]["mac_address"]
        self.model = self.settings["deviceInfo"]["model"]
        self.firmware_version = self.settings["deviceInfo"]["firmware_version"]

    def print_status(self):
        """Print current simulator status"""
        if not self.simulator:
            return

        status = self.simulator.get_simulation_status()
        
        print("\n" + "=" * 50)
        print("    FRIDGE SIMULATOR STATUS")
        print("=" * 50)
        print(f"Device ID: {self.device_id}")
        print(f"Model: {self.model}")
        print(f"MAC Address: {self.mac_address}")
        print(f"MQTT Connected: {'Yes' if self.mqtt_client.connected else 'No'}")
        print(f"Door State: {'OPEN' if status['door_open'] else 'CLOSED'}")
        print(f"Compressor State: {'ON' if status['compressor_on'] else 'OFF'}")
        print(f"Target Temperature: {status['target_temperature']}°C")
        print(f"Spoilage Active: {'Yes' if status['spoilage_active'] else 'No'}")
        print(f"Malfunction Active: {'Yes' if status['malfunction_active'] else 'No'}")
        print(f"Automatic Mode: {'Yes' if status['automatic_mode'] else 'No'}")
        print(f"Data Format: SenML")
        print("\nCurrent Sensor Values:")
        for sensor, value in status['sensors'].items():
            print(f"  {sensor.capitalize():12}: {value:6.2f}")
        
        print("=" * 50 + "\n")

    def sensor_simulation_loop(self):
        while self.running:
            try:
                self.simulator.generate_realistic_data()
                time.sleep(2)
            except Exception as e:
                print(f"[ERROR] Sensor simulation error: {e}")

    def mqtt_publish_loop(self):
        while self.running:
            try:
                self.simulator.publish_sensor_data()
                time.sleep(5)
            except Exception as e:
                print(f"[ERROR] MQTT publishing error: {e}")

    def heartbeat_loop(self):
        while self.running:
            try:
                # Calculate uptime
                uptime = time.time() # Simplified
                self.mqtt_client.publish_heartbeat(uptime)
                time.sleep(self.settings["telemetry"]["heartbeat_interval_s"])
            except Exception as e:
                print(f"[ERROR] Heartbeat error: {e}")

    def status_loop(self):
        while self.running:
            try:
                time.sleep(300)
                self.print_status()
            except Exception as e:
                print(f"[ERROR] Status loop error: {e}")

    def run(self):
        print("=" * 60)
        print("    SMARTCHILL FRIDGE SIMULATOR (MODULAR)")
        print("=" * 60)
        print(f"MAC Address: {self.mac_address}")
        print(f"Model: {self.model}")
        print(f"Firmware: {self.firmware_version}")
        print("=" * 60)

        # Step 1: Register with catalog
        print("\n[INIT] Step 1: Registering with catalog...")
        success, device_id = self.catalog_client.register()
        if not success:
            print("[INIT] Warning: Proceeding without catalog registration")
        
        self.device_id = device_id
        print(f"[INIT] Using Device ID: {self.device_id}")

        # Initialize Simulator Logic (needs settings)
        # We pass a dummy mqtt client initially or handle circular dependency
        # Better: Initialize MQTT client, then simulator, then set simulator in mqtt client
        # But MQTT client needs simulator for callbacks
        # Solution: Pass simulator to MQTT client constructor, but simulator needs mqtt client for publishing events
        # Let's use the pattern: Init MQTT(settings, None) -> Init Simulator(settings, mqtt) -> MQTT.set_simulator(simulator)
        
        # Actually, my MQTTClient takes simulator in __init__. 
        # And Simulator takes mqtt_client in __init__.
        # Circular dependency.
        # Let's break it. 
        # 1. Create MQTTClient(settings, simulator=None)
        # 2. Create Simulator(settings, mqtt_client)
        # 3. mqtt_client.simulator = simulator
        
        # Wait, I defined MQTTClient to take simulator in init. Let me check my code.
        # Yes: class MQTTClient: def __init__(self, settings, simulator):
        # And Simulator: class FridgeSimulatorLogic: def __init__(self, settings, mqtt_client):
        
        # I need to modify one of them to accept None or set it later.
        # I'll modify MQTTClient usage here to pass None first, then set it.
        # But I didn't add a set_simulator method to MQTTClient in previous step.
        # I'll rely on python's dynamic nature: mqtt_client.simulator = simulator
        
        # Step 2: Setup MQTT
        print("\n[INIT] Step 2: Setting up MQTT connection...")
        # Create placeholder
        class PlaceholderSimulator:
            def handle_simulation_command(self, cmd): pass
            
        self.mqtt_client = MQTTClient(self.settings, None) # Pass None for now
        self.mqtt_client.set_device_id(self.device_id)
        
        if not self.mqtt_client.start():
            print("[ERROR] Failed to setup MQTT connection")
            return

        # Step 3: Initialize Simulator
        self.simulator = FridgeSimulatorLogic(self.settings, self.mqtt_client)
        
        # Link simulator back to mqtt client
        self.mqtt_client.simulator = self.simulator

        print("\n[INIT] Simulator started successfully!")
        self.print_status()

        # Start threads
        threads = [
            threading.Thread(target=self.sensor_simulation_loop, daemon=True),
            threading.Thread(target=self.mqtt_publish_loop, daemon=True),
            threading.Thread(target=self.heartbeat_loop, daemon=True),
            threading.Thread(target=self.status_loop, daemon=True)
        ]

        for thread in threads:
            thread.start()

        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[SHUTDOWN] Received interrupt signal...")
            self.shutdown()

    def shutdown(self):
        print("[SHUTDOWN] Stopping fridge simulator...")
        self.running = False
        if self.mqtt_client:
            self.mqtt_client.stop()
        print("[SHUTDOWN] Stopped.")

if __name__ == "__main__":
    try:
        sim = FridgeSimulator()
        sim.run()
    except Exception as e:
        print(f"[FATAL] {e}")