import requests

class InfluxClient:
    def __init__(self, settings):
        self.settings = settings
        self.base_url = settings["influxdb_adaptor"]["base_url"]
        self.timeout = settings["influxdb_adaptor"]["timeout_seconds"]

    def fetch_historical_temperature(self, device_id, duration="30d"):
        """Fetch historical temperature data from InfluxDB Adaptor."""
        print(f"[DATA] Fetching historical temperature for {device_id} ({duration})")
        try:
            url = f"{self.base_url}/sensors/temperature"
            params = {"last": duration, "device": device_id}
            response = requests.get(url, params=params, timeout=self.timeout)
            if response.status_code == 200:
                senml_data = response.json()
                data_points = [{"timestamp": e["t"], "value": e["v"]} for e in senml_data.get("e", []) if 't' in e and 'v' in e]
                print(f"[DATA] Fetched {len(data_points)} historical temperature points")
                return data_points
            else:
                print(f"[DATA] Error fetching historical temperature: {response.status_code}")
                return []
        except requests.RequestException as e:
            print(f"[DATA] Error connecting to Adaptor for historical temperature: {e}")
            return []

    def fetch_historical_door_events(self, device_id, duration="30d"):
        """Fetch historical door events from InfluxDB Adaptor."""
        print(f"[DATA] Fetching historical door events for {device_id} ({duration})")
        try:
            url = f"{self.base_url}/events"
            params = {"device": device_id, "last": duration}
            response = requests.get(url, params=params, timeout=self.timeout)
            if response.status_code == 200:
                events_data = response.json()
                events = events_data.get("events", [])
                print(f"[DATA] Fetched {len(events)} historical door events")
                return events
            else:
                print(f"[DATA] Error fetching historical door events: {response.status_code}")
                return []
        except requests.RequestException as e:
            print(f"[DATA] Error connecting to Adaptor for historical door events: {e}")
            return []
