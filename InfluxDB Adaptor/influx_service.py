import json
import time
import threading
import requests
import random
import queue
import cherrypy
import os
from datetime import datetime, timezone
from influxdb_client import InfluxDBClient
from influxdb_client.client.write_api import SYNCHRONOUS

from MyMQTT import MyMQTT

from influx_utils import (
    parse_senml_payload, 
    validate_sensor_data, 
    create_influx_point, 
    create_door_event_point
)

class InfluxDBAdaptor:
    def __init__(self, settings_file="settings.json"):
        self.settings_file = settings_file
        self.settings = self.load_settings()
        
        # Service configuration from settings
        self.service_info = self.settings["serviceInfo"]
        self.service_id = self.service_info["serviceID"]
        self.catalog_url = self.settings["catalog"]["url"]
        
        # MQTT configuration
        self.mqtt_client = None
        self.broker_host = self.settings["mqtt"]["brokerIP"]
        self.broker_port = self.settings["mqtt"]["brokerPort"]
        self.connected = False
        
        # InfluxDB configuration
        self.influx_client = None
        self.write_api = None
        self.query_api = None
        self.data_queue = queue.Queue(maxsize=self.settings["defaults"]["max_queue_size"])
        
        # Device tracking
        self.known_devices = set()   # Cache of devices we know exist in catalog
        self.door_timers = {}        # {device_id: start_time} for tracking door open duration
        
        # REST API
        self.rest_server_thread = None
        
        # Threading
        self.running = True
        self.config_lock = threading.RLock()
        
        print(f"[INIT] {self.service_id} service starting...")
    
    def load_settings(self):
        """Load settings from JSON file"""
        try:
            with open(self.settings_file, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            print(f"[ERROR] Settings file {self.settings_file} not found")
            raise
        except json.JSONDecodeError as e:
            print(f"[ERROR] Invalid JSON in settings file: {e}")
            raise
    
    def save_settings(self):
        """Save current settings to file"""
        with self.config_lock:
            self.settings["lastUpdate"] = datetime.now(timezone.utc).isoformat()
            self.settings["configVersion"] += 1
            
            try:
                with open(self.settings_file, 'w') as f:
                    json.dump(self.settings, f, indent=4)
                print(f"[CONFIG] Settings saved to {self.settings_file}")
            except Exception as e:
                print(f"[ERROR] Failed to save settings: {e}")
    
    def load_influx_token(self):
        """Load InfluxDB token from settings or environment"""
        # Try to get token from settings first
        token = self.settings["influxdb"].get("token")
        if token and token != "YOUR_INFLUX_TOKEN_HERE":
            return token
        
        # Fallback to environment variable
        token = os.getenv("INFLUX_TOKEN")
        if token:
            print("[INFLUX] Using token from environment variable")
            return token
        
        # try token file if specified
        token_file = self.settings["influxdb"].get("token_file")
        if token_file:
            try:
                with open(token_file, 'r') as f:
                    return f.read().strip()
            except FileNotFoundError:
                print(f"[INFLUX] Token file not found: {token_file}")
        
        raise Exception("No InfluxDB token found in settings, environment, or file")
    
    def handle_door_event_senml(self, device_id, parsed_data):
        """Handle SenML door event data"""
        door_state = None
        duration = None
        timestamp = None
        
        # Extract door state and duration from parsed SenML data
        for data_entry in parsed_data:
            if data_entry["sensor_name"] == "door_state":
                door_state = data_entry["string_value"]
                timestamp = data_entry["timestamp"]
            elif data_entry["sensor_name"] == "door_duration":
                duration = data_entry["value"]
        
        if door_state == "door_opened":
            current_time = time.time()
            self.door_timers[device_id] = current_time
            print(f"[DOOR] Door OPENED for {device_id} - timer started")
            self.store_door_event(device_id, "door_opened", 1, datetime.fromtimestamp(timestamp, tz=timezone.utc))
            
        elif door_state == "door_closed":
            if device_id in self.door_timers:
                del self.door_timers[device_id]
            
            print(f"[DOOR] Door CLOSED for {device_id}")
            ts = datetime.fromtimestamp(timestamp, tz=timezone.utc) if timestamp else datetime.now(timezone.utc)
            self.store_door_event(device_id, "door_closed", 0, ts, duration)
    
    def store_door_event(self, device_id, event_type, value, timestamp, duration=None):
        """Store door event in InfluxDB"""
        try:
            point = create_door_event_point(
                self.settings["influxdb"]["measurement_name_events"],
                device_id, 
                event_type, 
                value, 
                timestamp, 
                duration
            )
            
            if not point:
                return False

            # Add to queue
            try:
                self.data_queue.put_nowait(point)
                print(f"[DOOR_DATA] Queued {event_type} event for {device_id}")
                return True
            except queue.Full:
                print(f"[QUEUE] Data queue full - dropping door event")
                return False
                
        except Exception as e:
            print(f"[DOOR_DATA] Error storing door event: {e}")
            return False

    def setup_influxdb(self):
        """Setup InfluxDB connection"""
        try:
            token = self.load_influx_token()
            url = self.settings["influxdb"]["url"]
            org = self.settings["influxdb"]["org"]
            
            self.influx_client = InfluxDBClient(url=url, token=token, org=org)
            self.write_api = self.influx_client.write_api(write_options=SYNCHRONOUS)
            self.query_api = self.influx_client.query_api()
            
            # Test connection
            health = self.influx_client.health()
            if health.status == "pass":
                print(f"[INFLUX] Connected to InfluxDB at {url}")
                print(f"[INFLUX] Organization: {org}")
                print(f"[INFLUX] Bucket: {self.settings['influxdb']['bucket']}")
                return True
            else:
                print(f"[INFLUX] InfluxDB health check failed: {health}")
                return False
                
        except Exception as e:
            print(f"[INFLUX] Failed to connect to InfluxDB: {e}")
            return False
    
    def extract_mqtt_topics(self):
        """Extract MQTT topics from service endpoints"""
        subscribe_topics = []
        
        for endpoint in self.service_info["endpoints"]:
            if endpoint.startswith("MQTT Subscribe: "):
                topic = endpoint.replace("MQTT Subscribe: ", "")
                subscribe_topics.append(topic)
        
        return subscribe_topics
    
    def register_with_catalog(self, max_retries=5, base_delay=2):
        """Register service with catalog via REST with retry logic"""
        for attempt in range(max_retries):
            try:
                # Add REST endpoints to service info
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
                exists = result.get("exists", False)
                
                if exists:
                    self.known_devices.add(device_id)
                    print(f"[DEVICE_CHECK] Device {device_id} confirmed in catalog")
                    return True
                else:
                    print(f"[DEVICE_CHECK] Device {device_id} not found in catalog")
                    return False
            else:
                print(f"[DEVICE_CHECK] Error checking device {device_id}: {response.status_code}")
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
            else:
                print(f"[INIT] Failed to load devices from catalog: {response.status_code}")
                return False
                
        except requests.RequestException as e:
            print(f"[INIT] Error loading devices from catalog: {e}")
            return False

    def store_sensor_data(self, device_id, sensor_type, value, timestamp):
        """Store sensor data in InfluxDB"""
        try:
            # Pass validation setting from config
            enable_val = self.settings["defaults"]["enable_data_validation"]
            if not validate_sensor_data(device_id, sensor_type, value, enable_val):
                return False
            
            # Pass measurement name from config
            measurement = self.settings["influxdb"]["measurement_name_sensors"]
            point = create_influx_point(measurement, device_id, sensor_type, value, timestamp)
            
            if not point:
                return False
            
            # Add to queue for batch processing
            try:
                self.data_queue.put_nowait(point)
                return True
            except queue.Full:
                print(f"[QUEUE] Data queue full - dropping data point")
                return False
                
        except Exception as e:
            print(f"[STORE] Error storing data: {e}")
            return False
    
    def batch_writer_loop(self):
        """Background thread for batch writing to InfluxDB"""
        batch = []
        last_flush = time.time()
        flush_interval = self.settings["influxdb"]["flush_interval_seconds"]
        batch_size = self.settings["influxdb"]["batch_size"]
        
        while self.running:
            try:
                # Try to get data from queue with timeout
                try:
                    point = self.data_queue.get(timeout=1)
                    batch.append(point)
                except queue.Empty:
                    pass
                
                current_time = time.time()
                should_flush = (
                    len(batch) >= batch_size or 
                    (batch and current_time - last_flush >= flush_interval)
                )
                
                if should_flush and batch:
                    try:
                        bucket = self.settings["influxdb"]["bucket"]
                        self.write_api.write(bucket=bucket, record=batch)
                        print(f"[INFLUX] Wrote {len(batch)} points to InfluxDB")
                        batch = []
                        last_flush = current_time
                    except Exception as e:
                        print(f"[INFLUX] Error writing batch to InfluxDB: {e}")
                        # Clear batch to avoid infinite retry
                        batch = []
                
            except Exception as e:
                print(f"[BATCH] Error in batch writer: {e}")
                time.sleep(5)
    
    def handle_config_update(self, topic, payload):
        """Handle configuration update via MQTT"""
        try:
            message = json.loads(payload)
            
            if message.get("type") == "influx_config_update":
                new_config = message.get("config", {})
                
                if new_config:
                    with self.config_lock:
                        # Update relevant settings
                        if "influxdb" in new_config:
                            self.settings["influxdb"].update(new_config["influxdb"])
                        if "defaults" in new_config:
                            self.settings["defaults"].update(new_config["defaults"])
                        
                        self.save_settings()
                    
                    # Acknowledge the update
                    ack_topic = f"Group17/SmartChill/InfluxDBAdaptor/config_ack"
                    ack_payload = {
                        "status": "updated",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "config_version": self.settings["configVersion"]
                    }
                    self.mqtt_client.myPublish(ack_topic, ack_payload)
                    
        except Exception as e:
            print(f"[CONFIG] Error processing config update: {e}")
    
    def notify(self, topic, payload):
        """Callback method for MyMQTT - handles incoming SenML sensor data and door events"""
        try:
            # Handle configuration updates
            if "config_update" in topic:
                self.handle_config_update(topic, payload)
                return
            
            parsed_data = parse_senml_payload(payload)
            if not parsed_data:
                print(f"[SENML] Failed to parse SenML data from topic: {topic}")
                return
            
            topic_parts = topic.split('/')
            
            # Expected topics: 
            # - Group17/SmartChill/Devices/{model}/{device_id}/{sensor_type}
            # - Group17/SmartChill/Devices/{model}/{device_id}/door_event
            if len(topic_parts) >= 5:
                topic_device_id = topic_parts[-2]  # Extract device_id from topic
                topic_type = topic_parts[-1]  # Extract sensor type or door_event
                
                # Process each entry in the SenML payload
                for data_entry in parsed_data:
                    device_id = data_entry["device_id"] or topic_device_id
                    
                    # Check if we know this device - if not, verify with catalog
                    if device_id not in self.known_devices:
                        print(f"[NEW_DEVICE] Unknown device detected: {device_id}")
                        if self.check_device_exists_in_catalog(device_id):
                            print(f"[NEW_DEVICE] Device {device_id} confirmed in catalog")
                        else:
                            print(f"[NEW_DEVICE] Device {device_id} not registered in catalog - ignoring data")
                            continue
                    
                    # Handle door events
                    if topic_type == "door_event":
                        self.handle_door_event_senml(device_id, parsed_data)
                        break  # Process all door event entries together
                    
                    # Handle sensor data
                    else:
                        sensor_name = data_entry["sensor_name"]
                        value = data_entry["value"]
                        timestamp = data_entry["timestamp"]
                        
                        if sensor_name == topic_type and value is not None:
                            # Convert timestamp to datetime
                            if timestamp:
                                try:
                                    ts = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                                except (ValueError, TypeError):
                                    ts = datetime.now(timezone.utc)
                            else:
                                ts = datetime.now(timezone.utc)
                            
                            # Store in InfluxDB
                            success = self.store_sensor_data(device_id, sensor_name, value, ts)
                            if success:
                                print(f"[SENML] Stored {sensor_name} data from {device_id}: {value}")
                            else:
                                print(f"[SENML] Failed to store {sensor_name} data from {device_id}")
            else:
                print(f"[WARN] Unexpected topic format: {topic}")
                
        except Exception as e:
            print(f"[ERROR] Error processing SenML message: {e}")
            import traceback
            traceback.print_exc()
    
    def setup_mqtt(self):
        """Setup MQTT client and subscribe to topics from service endpoints"""
        try:
            client_id = f"{self.settings['mqtt']['clientID_prefix']}_{int(time.time())}"
            self.mqtt_client = MyMQTT(client_id, self.broker_host, self.broker_port, self)
            
            # Start connection
            self.mqtt_client.start()
            time.sleep(2)
            self.connected = True
            
            # Extract and subscribe to topics from service endpoints
            subscribe_topics = self.extract_mqtt_topics()
            for topic in subscribe_topics:
                self.mqtt_client.mySubscribe(topic)
                print(f"[MQTT] Subscribed to: {topic}")
            
            print(f"[MQTT] Connected to broker {self.broker_host}:{self.broker_port}")
            return True
            
        except Exception as e:
            print(f"[MQTT] Connection error: {e}")
            return False
    
    # ============= REST API METHODS =============
    
    def query_door_events_from_influx(self, device_filter=None, duration="168h", *, limit=None):
        """Query door events from InfluxDB and return structured data"""
        try:
            bucket = self.settings["influxdb"]["bucket"]
            measurement = self.settings["influxdb"]["measurement_name_events"]
            
            query = f'''
            from(bucket: "{bucket}")
                |> range(start: -{duration})
                |> filter(fn: (r) => r._measurement == "{measurement}")
            '''
            
            if device_filter:
                query += f'''
                |> filter(fn: (r) => r.device_id == "{device_filter}")
                '''
            
            query += '''
                |> filter(fn: (r) => r._field == "value" or r._field == "duration_seconds")
            '''
            
            if isinstance(limit, int) and limit > 0:
                query += f'''
                |> sort(columns: ["_time"], desc: true)
                |> limit(n: {limit * 2})
                |> sort(columns: ["_time"])
                '''
            else:
                query += '''
                |> sort(columns: ["_time"])
                '''
            
            result = self.query_api.query(query)
            
            events_data = {}
            for table in result:
                for record in table.records:
                    timestamp = int(record.get_time().timestamp())
                    device_id = record.values.get("device_id")
                    event_type = record.values.get("event_type")
                    field = record.get_field()
                    value = record.get_value()
                    
                    key = (timestamp, device_id, event_type)
                    
                    if key not in events_data:
                        events_data[key] = {"timestamp": timestamp, "device_id": device_id, "event_type": event_type}
                    
                    if field == "value":
                        events_data[key]["value"] = value
                    elif field == "duration_seconds":
                        events_data[key]["duration"] = value
            
            events_list = []
            for event_data in events_data.values():
                events_list.append({
                    "timestamp": event_data["timestamp"],
                    "device_id": event_data["device_id"],
                    "event_type": event_data["event_type"],
                    "duration": event_data.get("duration")
                })
            
            events_list.sort(key=lambda x: x["timestamp"])
            
            print(f"[REST] Door events query returned {len(events_list)} events (device={device_filter}, duration={duration})")
            return {"events": events_list}
            
        except Exception as e:
            print(f"[REST] Error querying door events from InfluxDB: {e}")
            return {"events": []}
    
    def query_sensor_data_from_influx(self, sensor_type, device_filter=None, duration="24h", *, last=False, limit=None):
        """Query sensor data from InfluxDB and return in SenML-like format"""
        try:
            bucket = self.settings["influxdb"]["bucket"]
            measurement = self.settings["influxdb"]["measurement_name_sensors"]

            effective_duration = duration or ("365d" if last else "24h")

            query = f'''
            from(bucket: "{bucket}")
                |> range(start: -{effective_duration})
                |> filter(fn: (r) => r._measurement == "{measurement}")
                |> filter(fn: (r) => r.sensor_type == "{sensor_type}")
            '''

            if device_filter:
                query += f'''
                |> filter(fn: (r) => r.device_id == "{device_filter}")
                '''

            query += '''
                |> filter(fn: (r) => r._field == "value")
            '''

            if last:
                query += '''
                |> last()
                '''
            elif isinstance(limit, int) and limit > 0:
                query += '''
                |> sort(columns: ["_time"], desc: true)
                |> limit(n: %d)
                |> sort(columns: ["_time"])
                ''' % (limit)
            else:
                query += '''
                |> sort(columns: ["_time"])
                '''

            result = self.query_api.query(query)

            senml_data = {"e": []}
            count = 0

            for table in result:
                for record in table.records:
                    ts = int(record.get_time().timestamp())
                    val = record.get_value()
                    senml_data["e"].append({"t": ts, "v": val})
                    count += 1

            print(f"[REST] Query returned {count} data points for {sensor_type} (last={last}, limit={limit}, device={device_filter})")
            return senml_data

        except Exception as e:
            print(f"[REST] Error querying InfluxDB: {e}")
            return {"e": []}

    def setup_rest_api(self):
        """Setup REST API using CherryPy"""
        try:
            cherrypy.config.update({
                'server.socket_host': '0.0.0.0',
                'server.socket_port': 8002,
                'engine.autoreload.on': False,
                'log.screen': False
            })
            
            cherrypy.tree.mount(InfluxRestAPI(self), '/', {
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
                queue_size = self.data_queue.qsize()
                max_size = self.settings["defaults"]["max_queue_size"]
                
                if queue_size > max_size * 0.8:
                    print(f"[STATUS] Queue warning: {queue_size}/{max_size} ({queue_size/max_size*100:.1f}%)")
                elif queue_size > 0:
                    print(f"[STATUS] Queue size: {queue_size}/{max_size}")
                
                time.sleep(self.settings["catalog"]["ping_interval_seconds"])
                
            except Exception as e:
                print(f"[STATUS] Error in status monitor: {e}")
                time.sleep(30)
    
    def get_status(self):
        """Get current service status"""
        return {
            "service_id": self.service_id,
            "status": "running" if self.running else "stopped",
            "mqtt_connected": self.connected,
            "influx_connected": self.influx_client is not None,
            "rest_api_active": self.rest_server_thread is not None,
            "queue_size": self.data_queue.qsize(),
            "max_queue_size": self.settings["defaults"]["max_queue_size"],
            "known_devices": len(self.known_devices),
            "config_version": self.settings["configVersion"]
        }
    
    def run(self):
        """Main run method"""
        print("=" * 60)
        print("    SMARTCHILL INFLUXDB ADAPTOR SERVICE (SenML + REST)")
        print("=" * 60)
        
        print("[INIT] Setting up InfluxDB connection...")
        if not self.setup_influxdb():
            print("[ERROR] Failed to setup InfluxDB connection")
            return
        
        print("[INIT] Setting up REST API...")
        if not self.setup_rest_api():
            print("[WARN] Failed to setup REST API - continuing without it")
        
        print("[INIT] Registering service with catalog...")
        if not self.register_with_catalog():
            print("[WARN] Failed to register with catalog - continuing anyway")
        
        print("[INIT] Loading known devices from catalog...")
        self.load_known_devices_from_catalog()
        
        print("[INIT] Setting up MQTT connection...")
        if not self.setup_mqtt():
            print("[ERROR] Failed to setup MQTT connection")
            return
        
        print(f"[INIT] Service started successfully!")
        print(f"[INIT] Processing SenML formatted data.")
        print(f"[INIT] REST API available on port 8002")
        print(f"[INIT] Known devices from catalog: {len(self.known_devices)}")
        print(f"[INIT] Data validation: {self.settings['defaults']['enable_data_validation']}")
        
        batch_writer_thread = threading.Thread(target=self.batch_writer_loop, daemon=True)
        registration_thread = threading.Thread(target=self.periodic_registration, daemon=True)
        status_thread = threading.Thread(target=self.status_monitor_loop, daemon=True)
        
        batch_writer_thread.start()
        registration_thread.start()
        status_thread.start()
        
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[SHUTDOWN] Received interrupt signal...")
            self.shutdown()
    
    def shutdown(self):
        """Graceful shutdown"""
        print("[SHUTDOWN] Stopping InfluxDB Adaptor service...")
        self.running = False
        
        if self.rest_server_thread:
            try:
                cherrypy.engine.exit()
                print("[SHUTDOWN] REST API server stopped")
            except Exception as e:
                print(f"[SHUTDOWN] Error stopping REST API: {e}")
        
        print("[SHUTDOWN] Flushing remaining data...")
        remaining_points = []
        try:
            while not self.data_queue.empty():
                remaining_points.append(self.data_queue.get_nowait())
        except queue.Empty:
            pass
        
        if remaining_points and self.write_api:
            try:
                bucket = self.settings["influxdb"]["bucket"]
                self.write_api.write(bucket=bucket, record=remaining_points)
                print(f"[SHUTDOWN] Flushed {len(remaining_points)} remaining points")
            except Exception as e:
                print(f"[SHUTDOWN] Error flushing data: {e}")
        
        if self.mqtt_client:
            try:
                self.mqtt_client.stop()
                print("[SHUTDOWN] MQTT connection closed")
            except Exception as e:
                print(f"[SHUTDOWN] Error closing MQTT: {e}")
        
        if self.influx_client:
            try:
                self.influx_client.close()
                print("[SHUTDOWN] InfluxDB connection closed")
            except Exception as e:
                print(f"[SHUTDOWN] Error closing InfluxDB: {e}")
        
        print("[SHUTDOWN] InfluxDB Adaptor service stopped")


class InfluxRestAPI:
    """REST API endpoints for InfluxDB Adaptor"""
    
    def __init__(self, adaptor):
        self.adaptor = adaptor
    
    @cherrypy.expose
    @cherrypy.tools.json_out()
    def health(self):
        """GET /health - Health check endpoint"""
        try:
            if self.adaptor.influx_client:
                health = self.adaptor.influx_client.health()
                influx_healthy = health.status == "pass"
            else:
                influx_healthy = False
            
            return {
                "status": "healthy" if influx_healthy else "degraded",
                "service": "InfluxDB Adaptor",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "influx_connected": influx_healthy,
                "mqtt_connected": self.adaptor.connected,
                "queue_size": self.adaptor.data_queue.qsize()
            }
        except Exception as e:
            cherrypy.response.status = 500
            return {
                "status": "unhealthy",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
    
    @cherrypy.expose
    @cherrypy.tools.json_out()
    def status(self):
        """GET /status - Detailed service status"""
        return self.adaptor.get_status()
    
    @cherrypy.expose
    @cherrypy.tools.json_out()
    def events(self, **params):
        """GET /events?device={device_id}&last={duration}&limit=100"""
        try:
            device_filter = params.get("device", None)
            duration = params.get("last", "168h")
            limit = params.get("limit")
            
            if limit is not None:
                try:
                    limit = int(limit)
                except ValueError:
                    cherrypy.response.status = 400
                    return {"error": f"Invalid limit value: {limit}"}
            
            if not duration.endswith(('h', 'm', 'd', 's')):
                cherrypy.response.status = 400
                return {
                    "error": "Duration must end with h/m/d/s (e.g., '24h', '7d')",
                    "received": duration
                }
            
            print(f"[REST] Door events query: device={device_filter}, duration={duration}, limit={limit}")
            
            events_data = self.adaptor.query_door_events_from_influx(
                device_filter=device_filter,
                duration=duration,
                limit=limit
            )
            
            return events_data
            
        except Exception as e:
            print(f"[REST] Error in events endpoint: {e}")
            cherrypy.response.status = 500
            return {
                "error": "Internal server error",
                "details": str(e)
            }
        
    @cherrypy.expose
    @cherrypy.tools.json_out()
    def sensors(self, sensor_type, **params):
        """GET /sensors/{sensor_type}?last={duration}&device={device_id}&limit=10&last_only=true"""
        try:
            duration = params.get("last", "24h")
            device_filter = params.get("device", None)
            limit = params.get("limit")
            last_only = params.get("last_only", "false").lower() == "true"

            if limit is not None:
                try:
                    limit = int(limit)
                except ValueError:
                    cherrypy.response.status = 400
                    return {"error": f"Invalid limit value: {limit}"}

            valid_sensors = ["temperature", "humidity", "light", "gas"]
            if sensor_type not in valid_sensors:
                cherrypy.response.status = 400
                return {
                    "error": f"Invalid sensor type. Valid types: {valid_sensors}",
                    "received": sensor_type
                }
            
            if not last_only and not duration.endswith(('h', 'm', 'd', 's')):
                cherrypy.response.status = 400
                return {
                    "error": "Duration must end with h/m/d/s (e.g., '24h', '30m')",
                    "received": duration
                }
            
            print(f"[REST] Query: sensor={sensor_type}, duration={duration}, device={device_filter}, limit={limit}, last_only={last_only}")
            
            senml_data = self.adaptor.query_sensor_data_from_influx(
                sensor_type=sensor_type,
                device_filter=device_filter,
                duration=duration,
                last=last_only,
                limit=limit
            )
            
            return senml_data
            
        except Exception as e:
            print(f"[REST] Error in sensors endpoint: {e}")
            cherrypy.response.status = 500
            return {
                "error": "Internal server error",
                "details": str(e)
            }