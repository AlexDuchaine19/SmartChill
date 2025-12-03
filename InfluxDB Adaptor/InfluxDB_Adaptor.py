import time
import threading
import requests
import random
import cherrypy
from MyMQTT import MyMQTT
from modules.utils import load_settings
from modules.influx_client import InfluxClient
from modules.mqtt_handler import MQTTHandler
from modules.rest_api import InfluxRestAPI

class InfluxDBAdaptor:
    def __init__(self, settings_file="settings.json"):
        self.settings_file = settings_file
        self.settings = load_settings(settings_file)
        
        # Service configuration
        self.service_info = self.settings["serviceInfo"]
        self.service_id = self.service_info["serviceID"]
        self.catalog_url = self.settings["catalog"]["url"]
        
        # Components
        self.influx_client = InfluxClient(self.settings)
        self.mqtt_handler = MQTTHandler(self)
        
        # MQTT configuration
        self.mqtt_client = None
        self.broker_host = self.settings["mqtt"]["brokerIP"]
        self.broker_port = self.settings["mqtt"]["brokerPort"]
        self.connected = False
        
        # Device tracking
        self.known_devices = set()
        
        # REST API
        self.rest_server_thread = None
        
        # Threading
        self.running = True
        self.config_lock = threading.RLock()
        
        print(f"[INIT] {self.service_id} service starting...")
    
    def setup_mqtt(self):
        """Setup MQTT client and subscribe to topics from service endpoints"""
        try:
            client_id = f"{self.settings['mqtt']['clientID_prefix']}_{int(time.time())}"
            self.mqtt_client = MyMQTT(client_id, self.broker_host, self.broker_port, self.mqtt_handler)
            
            # Update handler with client reference
            self.mqtt_handler.mqtt_client = self.mqtt_client
            
            self.mqtt_client.start()
            time.sleep(2)
            self.connected = True
            
            subscribe_topics = []
            for endpoint in self.service_info["endpoints"]:
                if endpoint.startswith("MQTT Subscribe: "):
                    topic = endpoint.replace("MQTT Subscribe: ", "")
                    subscribe_topics.append(topic)
            
            for topic in subscribe_topics:
                self.mqtt_client.mySubscribe(topic)
                print(f"[MQTT] Subscribed to: {topic}")
            
            print(f"[MQTT] Connected to broker {self.broker_host}:{self.broker_port}")
            return True
            
        except Exception as e:
            print(f"[MQTT] Connection error: {e}")
            return False
    
    def setup_rest_api(self):
        """Setup REST API using CherryPy"""
        try:
            cherrypy.config.update({
                'server.socket_host': '0.0.0.0',
                'server.socket_port': 8002,
                'engine.autoreload.on': False,
                'log.screen': False
            })
            
            cherrypy.tree.mount(InfluxRestAPI(self.influx_client), '/', {
                '/': {
                    'tools.response_headers.on': True,
                    'tools.response_headers.headers': [('Content-Type', 'application/json')],
                }
            })
            
            def start_server():
                cherrypy.engine.start()
                print("[REST] REST API server started on port 8002")
            
            self.rest_server_thread = threading.Thread(target=start_server, daemon=True)
            self.rest_server_thread.start()
            time.sleep(2)
            
            return True
            
        except Exception as e:
            print(f"[REST] Failed to start REST API: {e}")
            return False

    def register_with_catalog(self, max_retries=5, base_delay=2):
        """Register service with catalog via REST"""
        for attempt in range(max_retries):
            try:
                endpoints = self.service_info["endpoints"] + [
                    "REST: GET /sensors/{sensor_type}?last={duration}&device={device_id}",
                    "REST: GET /health",
                    "REST: GET /status"
                ]
                
                registration_data = {
                    "serviceID": self.service_info["serviceID"],
                    "name": self.service_info["serviceName"],
                    "description": self.service_info["serviceDescription"],
                    "type": self.service_info["serviceType"],
                    "version": self.service_info["version"],
                    "endpoints": endpoints,
                    "status": "active"
                }
                
                response = requests.post(
                    f"{self.catalog_url}/services/register",
                    json=registration_data,
                    timeout=5
                )
                
                if response.status_code in [200, 201]:
                    print(f"[REGISTER] Successfully registered with catalog")
                    return True
                else:
                    print(f"[REGISTER] Failed to register (attempt {attempt+1}/{max_retries}): {response.status_code}")
                    
            except requests.RequestException as e:
                print(f"[REGISTER] Error registering (attempt {attempt+1}/{max_retries}): {e}")
            
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                print(f"[REGISTER] Retrying in {delay:.1f} seconds...")
                time.sleep(delay)
        
        return False

    def check_device_exists_in_catalog(self, device_id):
        """Check if device exists in catalog via REST API"""
        try:
            response = requests.get(f"{self.catalog_url}/devices/{device_id}/exists", timeout=5)
            if response.status_code == 200:
                result = response.json()
                if result.get("exists", False):
                    self.known_devices.add(device_id)
                    print(f"[DEVICE_CHECK] Device {device_id} confirmed in catalog")
                    return True
                else:
                    print(f"[DEVICE_CHECK] Device {device_id} not found in catalog")
                    return False
            return False
        except requests.RequestException as e:
            print(f"[DEVICE_CHECK] Error connecting to catalog: {e}")
            return False

    def load_known_devices_from_catalog(self):
        """Load all registered devices from catalog at startup"""
        try:
            response = requests.get(f"{self.catalog_url}/devices", timeout=5)
            if response.status_code == 200:
                devices = response.json()
                for device in devices:
                    device_id = device.get("deviceID")
                    if device_id and device_id.startswith("SmartChill_"):
                        self.known_devices.add(device_id)
                print(f"[INIT] Loaded {len(self.known_devices)} known devices from catalog")
                return True
            return False
        except requests.RequestException as e:
            print(f"[INIT] Error loading devices from catalog: {e}")
            return False

    def periodic_registration(self):
        """Periodically re-register with catalog"""
        interval = self.settings["catalog"]["registration_interval_seconds"]
        while self.running:
            time.sleep(interval)
            if self.running:
                print(f"[REGISTER] Periodic re-registration...")
                self.register_with_catalog()

    def status_monitor_loop(self):
        """Monitor service status and queue size"""
        while self.running:
            try:
                queue_size = self.influx_client.data_queue.qsize()
                max_size = self.settings["defaults"]["max_queue_size"]
                
                if queue_size > max_size * 0.8:
                    print(f"[STATUS] Queue warning: {queue_size}/{max_size}")
                elif queue_size > 0:
                    print(f"[STATUS] Queue size: {queue_size}/{max_size}")
                
                time.sleep(self.settings["catalog"]["ping_interval_seconds"])
            except Exception as e:
                print(f"[STATUS] Error in status monitor: {e}")
                time.sleep(30)

    def run(self):
        """Main run method"""
        print("=" * 60)
        print("    SMARTCHILL INFLUXDB ADAPTOR SERVICE (MODULAR)")
        print("=" * 60)
        
        print("[INIT] Setting up InfluxDB connection...")
        if not self.influx_client.connect():
            print("[ERROR] Failed to setup InfluxDB connection")
            return
        
        print("[INIT] Setting up REST API...")
        if not self.setup_rest_api():
            print("[WARN] Failed to setup REST API - continuing without it")
        
        print("[INIT] Setting up MQTT...")
        if not self.setup_mqtt():
            print("[ERROR] Failed to setup MQTT")
            return
        
        print("[INIT] Connecting to Catalog...")
        if not self.register_with_catalog():
            print("[WARN] Failed initial registration with catalog")
        
        self.load_known_devices_from_catalog()
        
        # Start background threads
        batch_thread = threading.Thread(target=self.influx_client.batch_writer_loop, daemon=True)
        batch_thread.start()
        
        reg_thread = threading.Thread(target=self.periodic_registration, daemon=True)
        reg_thread.start()
        
        monitor_thread = threading.Thread(target=self.status_monitor_loop, daemon=True)
        monitor_thread.start()
        
        print(f"[INIT] Service started successfully!")
        
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[SHUTDOWN] Service stopping...")
            self.running = False
            self.influx_client.running = False
            if self.mqtt_client:
                self.mqtt_client.stop()
            cherrypy.engine.exit()
            print("[SHUTDOWN] Service stopped")

if __name__ == "__main__":
    adaptor = InfluxDBAdaptor()
    adaptor.run()