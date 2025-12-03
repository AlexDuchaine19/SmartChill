import requests

class InfluxClient:
    def __init__(self, settings):
        self.settings = settings
        self.base_url = settings["influxdb_adaptor"]["base_url"]
        self.timeout = settings["influxdb_adaptor"]["timeout_seconds"]

    def fetch_sensor_data(self, device_id, sensor_type, duration):
        """Fetch sensor data from InfluxDB Adaptor via REST API"""
        try:
            url = f"{self.base_url}/sensors/{sensor_type}"
            params = {"last": duration, "device": device_id}
            
            response = requests.get(url, params=params, timeout=self.timeout)
            
            if response.status_code == 200:
                senml_data = response.json()
                entries = senml_data.get("e", [])
                
                # Convert SenML to simple data points
                data_points = []
                for entry in entries:
                    timestamp = entry.get("t")
                    value = entry.get("v")
                    if timestamp and value is not None:
                        data_points.append({"timestamp": timestamp, "value": value})
                
                print(f"[DATA] Fetched {len(data_points)} {sensor_type} points for {device_id}")
                return data_points
            else:
                print(f"[DATA] Error fetching {sensor_type} data: {response.status_code}")
                return []
                
        except requests.RequestException as e:
            print(f"[DATA] Error connecting to InfluxDB Adaptor for {sensor_type}: {e}")
            return []

    def fetch_door_events(self, device_id, duration):
        """Fetch door events from InfluxDB Adaptor via REST API"""
        try:
            url = f"{self.base_url}/events"
            params = {"device": device_id, "last": duration}
            
            response = requests.get(url, params=params, timeout=self.timeout)
            
            if response.status_code == 200:
                events_data = response.json()
                events = events_data.get("events", [])
                
                print(f"[DATA] Fetched {len(events)} door events for {device_id}")
                return events
            else:
                print(f"[DATA] Error fetching door events: {response.status_code}")
                return []
                
        except requests.RequestException as e:
            print(f"[DATA] Error connecting to InfluxDB Adaptor for door events: {e}")
            return []
