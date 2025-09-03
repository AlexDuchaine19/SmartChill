import json
import time
import threading
import requests
import os
from datetime import datetime, timezone
from MyMQTT import MyMQTT
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
import queue
import cherrypy

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
        self.door_timers = {}        # {device_id: start_time}

        # Threading
        self.running = True
        self.config_lock = threading.RLock()
        self.rest_server_thread = None

        print(f"[INIT] {self.service_id} service starting...")

    # -----------------------------
    # Settings / Config
    # -----------------------------
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
        """Save current settings to file (thread-safe)"""
        with self.config_lock:
            self.settings["lastUpdate"] = datetime.now(timezone.utc).isoformat()
            self.settings["configVersion"] += 1
            try:
                with open(self.settings_file, 'w') as f:
                    json.dump(self.settings, f, indent=4)
                print(f"[CONFIG] Settings saved to {self.settings_file}")
            except Exception as e:
                print(f"[ERROR] Failed to save settings: {e}")

    # -----------------------------
    # Influx token
    # -----------------------------
    def load_influx_token(self):
        """Load InfluxDB token from env, file, or settings"""
        # 1) env
        env_var = self.settings["influxdb"].get("token_env_var", "INFLUX_TOKEN")
        token = os.getenv(env_var)
        if token:
            print(f"[INFLUX] Token loaded from environment variable {env_var}")
            return token.strip()

        # 2) file (docker secret path)
        token_file = self.settings["influxdb"].get("token_file")
        if token_file:
            try:
                with open(token_file, 'r') as f:
                    print(f"[INFLUX] Token loaded from file {token_file}")
                    return f.read().strip()
            except FileNotFoundError:
                print(f"[INFLUX] Token file not found: {token_file}")

        # 3) inline in settings (se valorizzato)
        inline = self.settings["influxdb"].get("token")
        if inline:
            print("[INFLUX] Token loaded from settings.json (inline)")
            return inline.strip()

        raise RuntimeError("InfluxDB token not found (env, token_file, or settings.token)")

    # -----------------------------
    # Influx setup
    # -----------------------------
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
            if getattr(health, "status", "") == "pass":
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

    # -----------------------------
    # Catalog
    # -----------------------------
    def register_with_catalog(self):
        """Register service with catalog via REST"""
        try:
            # aggiungi anche gli endpoint REST
            rest_endpoints = [
                "REST: GET /health (Port: 8002)",
                "REST: GET /status (Port: 8002)",
                "REST: GET /sensors/{sensor_type}?device=ID&last=24h (Port: 8002)",
                "REST: GET /door_events?device=ID&last=24h (Port: 8002)",
                "REST: GET /config (Port: 8002)",
                "REST: PUT /config (Port: 8002)",
                "REST: POST /write_test (Port: 8002)",
            ]
            endpoints = list(self.service_info.get("endpoints", [])) + rest_endpoints

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
                print(f"[REGISTER] Failed to register: {response.status_code}")
                print(f"[REGISTER] Response: {response.text}")
                return False

        except requests.RequestException as e:
            print(f"[REGISTER] Error registering with catalog: {e}")
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

    # -----------------------------
    # MQTT
    # -----------------------------
    def extract_mqtt_topics(self):
        """Extract MQTT topics from service endpoints"""
        subscribe_topics = []
        for endpoint in self.service_info["endpoints"]:
            if endpoint.startswith("MQTT Subscribe: "):
                topic = endpoint.replace("MQTT Subscribe: ", "")
                subscribe_topics.append(topic)
        return subscribe_topics

    def setup_mqtt(self):
        """Setup MQTT client and subscribe to topics from service endpoints"""
        try:
            client_id = f"{self.settings['mqtt']['clientID_prefix']}_{int(time.time())}"
            self.mqtt_client = MyMQTT(client_id, self.broker_host, self.broker_port, self)

            # Start connection
            self.mqtt_client.start()
            time.sleep(2)
            self.connected = True

            # Subscribe
            for topic in self.extract_mqtt_topics():
                self.mqtt_client.mySubscribe(topic)
                print(f"[MQTT] Subscribed to: {topic}")

            print(f"[MQTT] Connected to broker {self.broker_host}:{self.broker_port}")
            return True

        except Exception as e:
            print(f"[MQTT] Connection error: {e}")
            return False

    # -----------------------------
    # Validation & storage
    # -----------------------------
    def validate_sensor_data(self, device_id, sensor_type, value, timestamp):
        """Validate sensor data before storing"""
        if not self.settings["defaults"]["enable_data_validation"]:
            return True

        rules = {
            "temperature": {"min": -50, "max": 100},
            "humidity": {"min": 0, "max": 100},
            "light": {"min": 0, "max": 100000},
            "gas": {"min": 0, "max": 1000}
        }
        if sensor_type in rules:
            r = rules[sensor_type]
            if not (r["min"] <= value <= r["max"]):
                print(f"[VALIDATION] Invalid {sensor_type} value from {device_id}: {value}")
                return False
        return True

    def create_influx_point_sensor(self, device_id, sensor_type, value, timestamp):
        """Create InfluxDB point for sensor data"""
        try:
            measurement = self.settings["influxdb"]["measurement_name_sensors"]
            point = (
                Point(measurement)
                .tag("device_id", device_id)
                .tag("sensor_type", sensor_type)
                .field("value", float(value))
                .time(timestamp, WritePrecision.S)
            )
            return point
        except Exception as e:
            print(f"[INFLUX] Error creating sensor point: {e}")
            return None

    def store_sensor_data(self, device_id, sensor_type, value, timestamp):
        """Store sensor data in queue"""
        try:
            if not self.validate_sensor_data(device_id, sensor_type, value, timestamp):
                return False
            point = self.create_influx_point_sensor(device_id, sensor_type, value, timestamp)
            if not point:
                return False
            self.data_queue.put_nowait(point)
            return True
        except queue.Full:
            print(f"[QUEUE] Data queue full - dropping sensor point")
            return False
        except Exception as e:
            print(f"[STORE] Error storing sensor data: {e}")
            return False

    # Door events
    def store_door_event(self, device_id, event_type, value, timestamp, duration=None):
        """Store door event in queue"""
        try:
            measurement = self.settings["influxdb"]["measurement_name_events"]
            point = (
                Point(measurement)
                .tag("device_id", device_id)
                .tag("event_type", event_type)
                .field("value", int(value))
                .time(timestamp, WritePrecision.S)
            )
            if duration is not None:
                point = point.field("duration_seconds", round(duration, 2))
            self.data_queue.put_nowait(point)
            print(f"[DOOR_DATA] Queued {event_type} for {device_id}")
            return True
        except queue.Full:
            print(f"[QUEUE] Data queue full - dropping door event")
            return False
        except Exception as e:
            print(f"[DOOR_DATA] Error storing door event: {e}")
            return False

    # -----------------------------
    # MQTT callback
    # -----------------------------
    def notify(self, topic, payload):
        """MQTT callback"""
        try:
            # Config updates via MQTT
            if "config_update" in topic:
                self.handle_config_update(topic, payload)
                return

            message = json.loads(payload)
            parts = topic.split('/')

            # Expected:
            # Group17/SmartChill/Devices/{model}/{device_id}/{sensor_type}
            # Group17/SmartChill/Devices/{model}/{device_id}/door_event
            if len(parts) >= 5:
                device_id = parts[-2]
                last_part = parts[-1]

                # Guard: check device
                if device_id not in self.known_devices:
                    print(f"[NEW_DEVICE] Unknown device: {device_id}")
                    if not self.check_device_exists_in_catalog(device_id):
                        print(f"[NEW_DEVICE] Device {device_id} not in catalog - ignoring")
                        return

                if last_part == "door_event":
                    event_type = message.get("event_type")
                    if event_type == "door_opened":
                        self.door_timers[device_id] = time.time()
                        print(f"[DOOR] OPENED for {device_id} - timer started")
                        self.store_door_event(device_id, "door_opened", 1, datetime.now(timezone.utc))
                    elif event_type == "door_closed":
                        dur = None
                        if device_id in self.door_timers:
                            dur = time.time() - self.door_timers[device_id]
                            del self.door_timers[device_id]
                            print(f"[DOOR] CLOSED for {device_id} after {dur:.1f}s")
                        else:
                            print(f"[DOOR] CLOSED for {device_id} (no timer)")
                        self.store_door_event(device_id, "door_closed", 0, datetime.now(timezone.utc), dur)
                    else:
                        print(f"[WARN] Unknown door event type: {event_type}")
                    return

                # sensor data
                sensor_type = last_part
                value = message.get("value")
                ts_str = message.get("timestamp")
                if value is None:
                    print(f"[WARN] No value in sensor message from {device_id}/{sensor_type}")
                    return

                if ts_str:
                    try:
                        ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                    except Exception:
                        ts = datetime.now(timezone.utc)
                else:
                    ts = datetime.now(timezone.utc)

                ok = self.store_sensor_data(device_id, sensor_type, float(value), ts)
                if ok:
                    print(f"[DATA] Stored {sensor_type} from {device_id}: {value}")
                else:
                    print(f"[DATA] Failed to store {sensor_type} from {device_id}")
            else:
                print(f"[WARN] Unexpected topic: {topic}")

        except json.JSONDecodeError as e:
            print(f"[ERROR] JSON decode error: {e}")
        except Exception as e:
            print(f"[ERROR] notify error: {e}")

    def handle_config_update(self, topic, payload):
        """Handle configuration update via MQTT"""
        try:
            msg = json.loads(payload)
            if msg.get("type") != "influx_config_update":
                return

            new_cfg = msg.get("config", {})
            if not new_cfg:
                return

            with self.config_lock:
                if "influxdb" in new_cfg:
                    # NON sovrascrivere token tramite MQTT per sicurezza
                    new_cfg["influxdb"].pop("token", None)
                    self.settings["influxdb"].update(new_cfg["influxdb"])
                if "defaults" in new_cfg:
                    self.settings["defaults"].update(new_cfg["defaults"])
                self.save_settings()

            # ACK
            ack_topic = "Group17/SmartChill/InfluxDBAdaptor/config_ack"
            ack_payload = {
                "status": "updated",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "config_version": self.settings["configVersion"]
            }
            self.mqtt_client.myPublish(ack_topic, ack_payload)

        except Exception as e:
            print(f"[CONFIG] Error processing config update: {e}")

    # -----------------------------
    # Batch writer
    # -----------------------------
    def batch_writer_loop(self):
        batch = []
        last_flush = time.time()
        flush_interval = self.settings["influxdb"]["flush_interval_seconds"]
        batch_size = self.settings["influxdb"]["batch_size"]

        while self.running:
            try:
                # consume
                try:
                    point = self.data_queue.get(timeout=1)
                    batch.append(point)
                except queue.Empty:
                    pass

                now = time.time()
                should_flush = (len(batch) >= batch_size) or (batch and now - last_flush >= flush_interval)

                if should_flush and batch:
                    try:
                        bucket = self.settings["influxdb"]["bucket"]
                        self.write_api.write(bucket=bucket, record=batch)
                        print(f"[INFLUX] Wrote {len(batch)} points")
                    except Exception as e:
                        print(f"[INFLUX] Error writing batch: {e}")
                    finally:
                        batch = []
                        last_flush = now

            except Exception as e:
                print(f"[BATCH] Error: {e}")
                time.sleep(5)

    # -----------------------------
    # Query helpers for REST
    # -----------------------------
    def query_sensor_data_from_influx(self, sensor_type, device_filter=None, duration="24h"):
        """Query sensors data -> SenML-like format"""
        try:
            bucket = self.settings["influxdb"]["bucket"]
            measurement = self.settings["influxdb"]["measurement_name_sensors"]

            query = f'''
            from(bucket: "{bucket}")
                |> range(start: -{duration})
                |> filter(fn: (r) => r._measurement == "{measurement}")
                |> filter(fn: (r) => r.sensor_type == "{sensor_type}")
                |> filter(fn: (r) => r._field == "value")
            '''
            if device_filter:
                query += f'''
                |> filter(fn: (r) => r.device_id == "{device_filter}")
                '''
            query += '''
                |> sort(columns: ["_time"])
            '''
            result = self.query_api.query(query)

            senml = {"e": []}
            for table in result:
                for rec in table.records:
                    ts = int(rec.get_time().timestamp())
                    val = rec.get_value()
                    senml["e"].append({"t": ts, "v": float(val)})
            return senml
        except Exception as e:
            print(f"[REST] Error querying sensor data: {e}")
            return {"e": []}

    def query_door_events_from_influx(self, device_filter=None, duration="24h"):
        """Query door events -> SenML-like format with vs field = event_type"""
        try:
            bucket = self.settings["influxdb"]["bucket"]
            measurement = self.settings["influxdb"]["measurement_name_events"]

            query = f'''
            from(bucket: "{bucket}")
                |> range(start: -{duration})
                |> filter(fn: (r) => r._measurement == "{measurement}")
                |> filter(fn: (r) => r._field == "value")
            '''
            if device_filter:
                query += f'''
                |> filter(fn: (r) => r.device_id == "{device_filter}")
                '''
            query += '''
                |> sort(columns: ["_time"])
            '''
            result = self.query_api.query(query)

            senml = {"e": []}
            for table in result:
                for rec in table.records:
                    ts = int(rec.get_time().timestamp())
                    event_type = rec.values.get("event_type")
                    item = {"t": ts, "vs": event_type}
                    dur = rec.values.get("duration_seconds")
                    if dur is not None:
                        try:
                            item["vd"] = float(dur)
                        except:
                            pass
                    senml["e"].append(item)
            return senml
        except Exception as e:
            print(f"[REST] Error querying door events: {e}")
            return {"e": []}

    # -----------------------------
    # REST API (CherryPy)
    # -----------------------------
    def setup_rest_api(self):
        """Start CherryPy REST server in a daemon thread"""
        try:
            cherrypy.config.update({
                'server.socket_host': '0.0.0.0',
                'server.socket_port': 8002,
                'engine.autoreload.on': False,
                'log.screen': False
            })

            # Simple CORS
            def cors():
                cherrypy.response.headers["Access-Control-Allow-Origin"] = "*"
                cherrypy.response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, OPTIONS"
                cherrypy.response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            cherrypy.tools.cors = cherrypy.Tool('before_handler', cors)

            conf = {
                '/': {
                    'tools.response_headers.on': True,
                    'tools.response_headers.headers': [('Content-Type', 'application/json')],
                    'tools.cors.on': True,
                    'tools.json_out.on': True
                }
            }

            cherrypy.tree.mount(InfluxRestAPI(self), '/', conf)

            def start_server():
                cherrypy.engine.start()
                print("[REST] REST API server started on port 8002")

            self.rest_server_thread = threading.Thread(target=start_server, daemon=True)
            self.rest_server_thread.start()
            time.sleep(1)
            return True
        except Exception as e:
            print(f"[REST] Failed to start REST API: {e}")
            return False

    # -----------------------------
    # Monitoring / status
    # -----------------------------
    def periodic_registration(self):
        """Periodically re-register with catalog"""
        interval = self.settings["catalog"]["registration_interval_seconds"]
        while self.running:
            time.sleep(interval)
            if self.running:
                print("[REGISTER] Periodic re-registration...")
                self.register_with_catalog()

    def status_monitor_loop(self):
        """Monitor queue/backpressure"""
        while self.running:
            try:
                qs = self.data_queue.qsize()
                maxq = self.settings["defaults"]["max_queue_size"]
                if qs > maxq * 0.8:
                    print(f"[STATUS] Queue warning: {qs}/{maxq} ({qs/maxq*100:.1f}%)")
                elif qs > 0:
                    print(f"[STATUS] Queue size: {qs}/{maxq}")
                time.sleep(self.settings["catalog"]["ping_interval_seconds"])
            except Exception as e:
                print(f"[STATUS] Error: {e}")
                time.sleep(30)

    def get_status(self):
        return {
            "service_id": self.service_id,
            "status": "running" if self.running else "stopped",
            "mqtt_connected": self.connected,
            "influx_connected": self.influx_client is not None,
            "queue_size": self.data_queue.qsize(),
            "max_queue_size": self.settings["defaults"]["max_queue_size"],
            "known_devices": len(self.known_devices),
            "config_version": self.settings["configVersion"]
        }

    # -----------------------------
    # Main
    # -----------------------------
    def run(self):
        print("=" * 60)
        print("    SMARTCHILL INFLUXDB ADAPTOR SERVICE")
        print("=" * 60)

        print("[INIT] Setting up InfluxDB connection...")
        if not self.setup_influxdb():
            print("[ERROR] Failed to setup InfluxDB connection")
            return

        print("[INIT] Registering service with catalog...")
        if not self.register_with_catalog():
            print("[WARN] Failed to register with catalog - continuing anyway")

        print("[INIT] Loading known devices from catalog...")
        self.load_known_devices_from_catalog()

        print("[INIT] Setting up MQTT connection...")
        if not self.setup_mqtt():
            print("[ERROR] Failed to setup MQTT connection")
            return

        print("[INIT] Starting REST API...")
        self.setup_rest_api()

        print(f"[INIT] Service started successfully!")
        print(f"[INIT] Known devices: {len(self.known_devices)}")
        print(f"[INIT] Data validation: {self.settings['defaults']['enable_data_validation']}")

        # Threads
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
        print("[SHUTDOWN] Stopping InfluxDB Adaptor service...")
        self.running = False

        # Flush remaining data
        print("[SHUTDOWN] Flushing remaining data...")
        remaining = []
        try:
            while not self.data_queue.empty():
                remaining.append(self.data_queue.get_nowait())
        except queue.Empty:
            pass

        if remaining and self.write_api:
            try:
                bucket = self.settings["influxdb"]["bucket"]
                self.write_api.write(bucket=bucket, record=remaining)
                print(f"[SHUTDOWN] Flushed {len(remaining)} remaining points")
            except Exception as e:
                print(f"[SHUTDOWN] Error flushing data: {e}")

        # Close MQTT
        if self.mqtt_client:
            try:
                self.mqtt_client.stop()
                print("[SHUTDOWN] MQTT connection closed")
            except Exception as e:
                print(f"[SHUTDOWN] MQTT close error: {e}")

        # Stop REST
        try:
            if cherrypy.engine.state == cherrypy.engine.states.STARTED:
                cherrypy.engine.exit()
                print("[SHUTDOWN] REST API stopped")
        except Exception as e:
            print(f"[SHUTDOWN] REST stop error: {e}")

        # Close Influx
        if self.influx_client:
            try:
                self.influx_client.close()
                print("[SHUTDOWN] InfluxDB connection closed")
            except Exception as e:
                print(f"[SHUTDOWN] Influx close error: {e}")

        print("[SHUTDOWN] InfluxDB Adaptor service stopped")


# -----------------------------
# CherryPy Controller
# -----------------------------
class InfluxRestAPI(object):
    def __init__(self, svc: InfluxDBAdaptor):
        self.svc = svc

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def health(self):
        return {
            "service": self.svc.service_id,
            "status": "ok",
            "mqtt_connected": self.svc.connected,
            "influx_connected": self.svc.influx_client is not None,
            "time": datetime.now(timezone.utc).isoformat()
        }

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def status(self):
        return self.svc.get_status()

    # GET /sensors/<sensor_type>?device=ID&last=24h  (or from/to)
    @cherrypy.expose
    @cherrypy.tools.json_out()
    def sensors(self, sensor_type=None, **params):
        if not sensor_type:
            cherrypy.response.status = 400
            return {"error": "sensor_type path param required (e.g. /sensors/temperature?last=6h)"}

        device = params.get("device")
        last = params.get("last", "24h")
        date_from = params.get("from")
        date_to = params.get("to")

        if date_from and date_to:
            try:
                dt_from = datetime.fromisoformat(date_from.replace('Z', '+00:00'))
                dt_to = datetime.fromisoformat(date_to.replace('Z', '+00:00'))
                delta = max(1, int((dt_to - dt_from).total_seconds()))
                last = f"{delta}s"
            except Exception:
                pass

        data = self.svc.query_sensor_data_from_influx(sensor_type, device_filter=device, duration=last)
        return {"sensor_type": sensor_type, "device": device, "last": last, "data": data}

    # GET /door_events?device=ID&last=24h
    @cherrypy.expose
    @cherrypy.tools.json_out()
    def door_events(self, **params):
        device = params.get("device")
        last = params.get("last", "24h")
        data = self.svc.query_door_events_from_influx(device_filter=device, duration=last)
        return {"device": device, "last": last, "data": data}

    # GET /config  |  PUT /config
    @cherrypy.expose
    @cherrypy.tools.json_out()
    def config(self, **_params):
        if cherrypy.request.method == "GET":
            safe = dict(self.svc.settings)
            if "influxdb" in safe and "token" in safe["influxdb"]:
                safe["influxdb"] = dict(safe["influxdb"])
                if safe["influxdb"].get("token"):
                    safe["influxdb"]["token"] = "***"
            return safe

        if cherrypy.request.method == "PUT":
            try:
                raw = cherrypy.request.body.read()
                new_cfg = json.loads(raw) if raw else {}
            except Exception:
                cherrypy.response.status = 400
                return {"error": "invalid JSON"}

            allowed_top = {"influxdb", "defaults"}
            with self.svc.config_lock:
                for k in list(new_cfg.keys()):
                    if k not in allowed_top:
                        del new_cfg[k]
                if "influxdb" in new_cfg:
                    new_cfg["influxdb"].pop("token", None)
                    self.svc.settings["influxdb"].update(new_cfg["influxdb"])
                if "defaults" in new_cfg:
                    self.svc.settings["defaults"].update(new_cfg["defaults"])
                self.svc.save_settings()

            return {"status": "updated", "config_version": self.svc.settings["configVersion"]}

        cherrypy.response.status = 405
        return {"error": "method not allowed"}

    # POST /write_test
    @cherrypy.expose
    @cherrypy.tools.json_out()
    def write_test(self):
        if cherrypy.request.method != "POST":
            cherrypy.response.status = 405
            return {"error": "method not allowed"}

        try:
            payload = cherrypy.request.body.read()
            body = json.loads(payload) if payload else {}
            device_id = body.get("device_id", "TEST_DEVICE")
            sensor_type = body.get("sensor_type", "temperature")
            value = float(body.get("value", 1.23))
            ts = datetime.now(timezone.utc)
        except Exception as e:
            cherrypy.response.status = 400
            return {"error": f"bad payload: {e}"}

        ok = self.svc.store_sensor_data(device_id, sensor_type, value, ts)
        return {"written": ok}


def main():
    svc = InfluxDBAdaptor()
    try:
        svc.run()
    except Exception as e:
        print(f"[FATAL] Service error: {e}")
    finally:
        svc.shutdown()


if __name__ == "__main__":
    main()
