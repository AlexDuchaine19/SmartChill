import os
import time
import queue
from datetime import datetime, timezone
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

class InfluxClient:
    def __init__(self, settings):
        self.settings = settings
        self.influx_client = None
        self.write_api = None
        self.query_api = None
        self.data_queue = queue.Queue(maxsize=self.settings["defaults"]["max_queue_size"])
        self.running = True

    def load_influx_token(self):
        """Load InfluxDB token from settings or environment"""
        token = self.settings["influxdb"].get("token")
        if token and token != "YOUR_INFLUX_TOKEN_HERE":
            return token
        
        token = os.getenv("INFLUX_TOKEN")
        if token:
            print("[INFLUX] Using token from environment variable")
            return token
        
        token_file = self.settings["influxdb"].get("token_file")
        if token_file:
            try:
                with open(token_file, 'r') as f:
                    return f.read().strip()
            except FileNotFoundError:
                print(f"[INFLUX] Token file not found: {token_file}")
        
        raise Exception("No InfluxDB token found in settings, environment, or file")

    def connect(self):
        """Setup InfluxDB connection"""
        try:
            token = self.load_influx_token()
            url = self.settings["influxdb"]["url"]
            org = self.settings["influxdb"]["org"]
            
            self.influx_client = InfluxDBClient(url=url, token=token, org=org)
            self.write_api = self.influx_client.write_api(write_options=SYNCHRONOUS)
            self.query_api = self.influx_client.query_api()
            
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

    def validate_sensor_data(self, device_id, sensor_type, value):
        """Validate sensor data before storing"""
        if not self.settings["defaults"]["enable_data_validation"]:
            return True
        
        validation_rules = {
            "temperature": {"min": -50, "max": 100},
            "humidity": {"min": 0, "max": 100},
            "light": {"min": 0, "max": 100000},
            "gas": {"min": 0, "max": 1000}
        }
        
        if sensor_type in validation_rules:
            rules = validation_rules[sensor_type]
            if not (rules["min"] <= value <= rules["max"]):
                print(f"[VALIDATION] Invalid {sensor_type} value from {device_id}: {value}")
                return False
        
        return True

    def store_sensor_data(self, device_id, sensor_type, value, timestamp):
        """Store sensor data in InfluxDB"""
        try:
            if not self.validate_sensor_data(device_id, sensor_type, value):
                return False
            
            point = Point(self.settings["influxdb"]["measurement_name_sensors"]) \
                .tag("device_id", device_id) \
                .tag("sensor_type", sensor_type) \
                .field("value", float(value)) \
                .time(timestamp, WritePrecision.S)
            
            try:
                self.data_queue.put_nowait(point)
                return True
            except queue.Full:
                print(f"[QUEUE] Data queue full - dropping data point")
                return False
                
        except Exception as e:
            print(f"[STORE] Error storing data: {e}")
            return False

    def store_door_event(self, device_id, event_type, value, timestamp, duration=None):
        """Store door event in InfluxDB"""
        try:
            point = Point(self.settings["influxdb"]["measurement_name_events"]) \
                .tag("device_id", device_id) \
                .tag("event_type", event_type) \
                .field("value", value) \
                .time(timestamp, WritePrecision.S)
            
            if duration is not None:
                point = point.field("duration_seconds", round(duration, 2))
            
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

    def batch_writer_loop(self):
        """Background thread for batch writing to InfluxDB"""
        batch = []
        last_flush = time.time()
        flush_interval = self.settings["influxdb"]["flush_interval_seconds"]
        batch_size = self.settings["influxdb"]["batch_size"]
        
        while self.running:
            try:
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
                        batch = []
                
            except Exception as e:
                print(f"[BATCH] Error in batch writer: {e}")
                time.sleep(5)

    def query_door_events(self, device_filter=None, duration="168h", limit=None):
        """Query door events from InfluxDB"""
        try:
            bucket = self.settings["influxdb"]["bucket"]
            measurement = self.settings["influxdb"]["measurement_name_events"]

            query = f'''
            from(bucket: "{bucket}")
                |> range(start: -{duration})
                |> filter(fn: (r) => r._measurement == "{measurement}")
            '''

            if device_filter:
                query += f'|> filter(fn: (r) => r.device_id == "{device_filter}")'

            query += '|> filter(fn: (r) => r._field == "value" or r._field == "duration_seconds")'

            if isinstance(limit, int) and limit > 0:
                query += f'''
                    |> sort(columns: ["_time"], desc: true)
                    |> limit(n: {limit * 2})
                    |> sort(columns: ["_time"])
                '''
            else:
                query += '|> sort(columns: ["_time"])'

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
                        events_data[key] = {
                            "timestamp": timestamp,
                            "device_id": device_id,
                            "event_type": event_type,
                            "duration": None,
                            "value": None
                        }

                    if field == "value":
                        events_data[key]["value"] = value
                    elif field == "duration_seconds":
                        events_data[key]["duration"] = value

            events_list = []
            for event in events_data.values():
                events_list.append({
                    "timestamp": event["timestamp"],
                    "device_id": event["device_id"],
                    "event_type": event["event_type"],
                    "duration": event["duration"]
                })

            return {"events": events_list}

        except Exception as e:
            print(f"[REST] Error querying door events: {e}")
            return {"events": []}

    def query_sensor_data(self, sensor_type, device_filter=None, duration="24h", last=False, limit=None):
        """Query sensor data from InfluxDB"""
        try:
            bucket = self.settings["influxdb"]["bucket"]
            measurement = self.settings["influxdb"]["measurement_name_sensors"]

            query = f'''
            from(bucket: "{bucket}")
                |> range(start: -{duration})
                |> filter(fn: (r) => r._measurement == "{measurement}")
                |> filter(fn: (r) => r.sensor_type == "{sensor_type}")
            '''

            if device_filter:
                query += f'|> filter(fn: (r) => r.device_id == "{device_filter}")'

            query += '|> filter(fn: (r) => r._field == "value")'

            if last:
                query += '|> last()'
            elif isinstance(limit, int) and limit > 0:
                query += f'''
                    |> sort(columns: ["_time"], desc: true)
                    |> limit(n: {limit})
                    |> sort(columns: ["_time"])
                '''
            else:
                query += '|> sort(columns: ["_time"])'

            result = self.query_api.query(query)

            senml_data = {"e": []}
            count = 0

            for table in result:
                for record in table.records:
                    timestamp = int(record.get_time().timestamp())
                    value = record.get_value()
                    senml_data["e"].append({"t": timestamp, "v": value})
                    count += 1

            print(f"[REST] Query returned {count} data points for {sensor_type}")
            return senml_data

        except Exception as e:
            print(f"[REST] Error querying InfluxDB: {e}")
            return {"e": []}
