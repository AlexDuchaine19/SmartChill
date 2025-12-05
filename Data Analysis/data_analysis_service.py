import json
import time
import threading
import requests
import random
import cherrypy
from datetime import datetime, timezone
import warnings

# Importiamo la logica pura dal file precedente
from analysis_logic import (
    analyze_temperature_data, 
    analyze_door_usage, 
    analyze_trends, 
    period_to_days
)

warnings.filterwarnings('ignore')

class DataAnalysisService:
    def __init__(self, settings_file="settings.json"):
        self.settings_file = settings_file
        self.settings = self.load_settings()
        
        # Service configuration from settings
        self.service_info = self.settings["serviceInfo"]
        self.service_id = self.service_info["serviceID"]
        self.catalog_url = self.settings["catalog"]["url"]
        self.influx_adaptor_url = self.settings["influxdb_adaptor"]["base_url"]
        
        # Device management
        self.known_devices = set()
        
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
    
    def register_with_catalog(self, max_retries=5, base_delay=2):
        """Register service with catalog via REST with retry logic"""
        for attempt in range(max_retries):
            try:
                registration_data = {
                "serviceID": self.service_info["serviceID"],
                "name": self.service_info["serviceName"],
                "description": self.service_info["serviceDescription"],
                "type": self.service_info["serviceType"],
                "version": self.service_info["version"],
                "endpoints": self.service_info["endpoints"],
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
    
    def validate_period(self, period):
        """Validate if the requested period is supported"""
        supported = self.settings["analysis"]["supported_periods"]
        return period in supported
    
    def fetch_sensor_data_from_adaptor(self, device_id, sensor_type, duration):
        """Fetch sensor data from InfluxDB Adaptor via REST API"""
        try:
            timeout = self.settings["influxdb_adaptor"]["timeout_seconds"]
            url = f"{self.influx_adaptor_url}/sensors/{sensor_type}"
            params = {"last": duration, "device": device_id}
            
            response = requests.get(url, params=params, timeout=timeout)
            
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
    
    def fetch_door_events_from_adaptor(self, device_id, duration):
        """Fetch door events from InfluxDB Adaptor via REST API"""
        try:
            timeout = self.settings["influxdb_adaptor"]["timeout_seconds"]
            url = f"{self.influx_adaptor_url}/events"
            params = {"device": device_id, "last": duration}
            
            response = requests.get(url, params=params, timeout=timeout)
            
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
    
    def perform_full_analysis(self, device_id, period, metrics_list):
        """Perform complete analysis for a device using imported logic"""
        print(f"[ANALYSIS] Starting full analysis for {device_id} (period: {period})")
        
        result = {
            "device_id": device_id,
            "period": period,
            "metrics_requested": metrics_list,
            "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
            "service": self.service_id
        }
        
        # Fetch data from InfluxDB Adaptor
        temp_data = []
        door_events = []
        
        if "temperature" in metrics_list:
            temp_data = self.fetch_sensor_data_from_adaptor(device_id, "temperature", period)
            # CALL TO IMPORTED FUNCTION
            result["temperature_analysis"] = analyze_temperature_data(temp_data, period)
        
        if "usage_patterns" in metrics_list:
            door_events = self.fetch_door_events_from_adaptor(device_id, period)
            # CALL TO IMPORTED FUNCTION
            result["usage_analysis"] = analyze_door_usage(door_events, period)
        
        if "trends" in metrics_list:
            if not temp_data:
                temp_data = self.fetch_sensor_data_from_adaptor(device_id, "temperature", period)
            if not door_events:
                door_events = self.fetch_door_events_from_adaptor(device_id, period)
            
            # CALL TO IMPORTED FUNCTION
            result["trends"] = analyze_trends(temp_data, door_events, period)
        
        # Add data summary
        result["data_summary"] = {
            "temperature_points": len(temp_data),
            "door_events": len(door_events),
            # CALL TO IMPORTED FUNCTION
            "period_days": period_to_days(period)
        }
        
        print(f"[ANALYSIS] Completed analysis for {device_id}: "
              f"{len(temp_data)} temp points, {len(door_events)} door events")
        
        return result
    
    def setup_rest_api(self):
        """Setup REST API using CherryPy"""
        try:
            cherrypy.config.update({
                'server.socket_host': '0.0.0.0',
                'server.socket_port': 8004,
                'engine.autoreload.on': False,
                'log.screen': False
            })
            
            cherrypy.tree.mount(DataAnalysisRestAPI(self), '/', {
                '/': {
                    'tools.response_headers.on': True,
                    'tools.response_headers.headers': [('Content-Type', 'application/json')],
                }
            })
            
            def start_server():
                cherrypy.engine.start()
                print("[REST] REST API server started on port 8004")
            
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
    
    def get_status(self):
        """Get current service status"""
        return {
            "service_id": self.service_id,
            "status": "running" if self.running else "stopped",
            "rest_api_active": self.rest_server_thread is not None,
            "known_devices": len(self.known_devices),
            "supported_periods": self.settings["analysis"]["supported_periods"],
            "config_version": self.settings["configVersion"]
        }
    
    def run(self):
        """Main run method"""
        print("=" * 60)
        print("    SMARTCHILL DATA ANALYSIS SERVICE")
        print("=" * 60)
        
        # Setup REST API
        print("[INIT] Setting up REST API...")
        if not self.setup_rest_api():
            print("[ERROR] Failed to setup REST API")
            return
        
        # Register with catalog
        print("[INIT] Registering service with catalog...")
        if not self.register_with_catalog():
            print("[WARN] Failed to register with catalog - continuing anyway")
        
        # Load known devices from catalog
        print("[INIT] Loading known devices from catalog...")
        self.load_known_devices_from_catalog()
        
        print(f"[INIT] Service started successfully!")
        print(f"[INIT] REST API available on port 8004")
        print(f"[INIT] Known devices: {len(self.known_devices)}")
        print(f"[INIT] Supported periods: {self.settings['analysis']['supported_periods']}")
        
        # Start background thread
        registration_thread = threading.Thread(target=self.periodic_registration, daemon=True)
        registration_thread.start()
        
        # Main loop
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[SHUTDOWN] Received interrupt signal...")
            self.shutdown()
    
    def shutdown(self):
        """Graceful shutdown"""
        print("[SHUTDOWN] Stopping Data Analysis service...")
        self.running = False
        
        if self.rest_server_thread:
            try:
                cherrypy.engine.exit()
                print("[SHUTDOWN] REST API server stopped")
            except Exception as e:
                print(f"[SHUTDOWN] Error stopping REST API: {e}")
        
        print("[SHUTDOWN] Data Analysis service stopped")

# ============= REST API CLASS =============

class DataAnalysisRestAPI:
    """REST API endpoints for Data Analysis Service"""
    
    def __init__(self, service):
        self.service = service
    
    @cherrypy.expose
    @cherrypy.tools.json_out()
    def health(self):
        """GET /health - Health check endpoint"""
        try:
            return {
                "status": "healthy",
                "service": "Data Analysis",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "known_devices": len(self.service.known_devices)
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
        return self.service.get_status()
    
    @cherrypy.expose
    @cherrypy.tools.json_out()
    def analyze(self, device_id, **params):
        """GET /analyze/{device_id}?period={duration}&metrics={list}"""
        try:
            # Check if device exists
            if device_id not in self.service.known_devices:
                if not self.service.check_device_exists_in_catalog(device_id):
                    cherrypy.response.status = 404
                    return {
                        "error": f"Device {device_id} not found in catalog",
                        "known_devices": list(self.service.known_devices)
                    }
            
            # Extract parameters
            period = params.get("period", self.service.settings["defaults"]["default_period"])
            metrics = params.get("metrics", "temperature,usage_patterns,trends")
            
            # Validate period
            if not self.service.validate_period(period):
                cherrypy.response.status = 400
                return {
                    "error": f"Unsupported period: {period}",
                    "supported_periods": self.service.settings["analysis"]["supported_periods"]
                }
            
            # Convert metrics to list
            metrics_list = metrics.split(",") if isinstance(metrics, str) else metrics
            
            # Perform analysis
            result = self.service.perform_full_analysis(device_id, period, metrics_list)
            
            return result
            
        except ValueError as e:
            cherrypy.response.status = 400
            return {"error": str(e)}
        except Exception as e:
            print(f"[REST] Error in analyze endpoint: {e}")
            cherrypy.response.status = 500
            return {
                "error": "Internal server error",
                "details": str(e)
            }
    
    @cherrypy.expose
    @cherrypy.tools.json_out()
    def trends(self, device_id, **params):
        """GET /trends/{device_id}?period={duration}"""
        try:
            # Check if device exists
            if device_id not in self.service.known_devices:
                if not self.service.check_device_exists_in_catalog(device_id):
                    cherrypy.response.status = 404
                    return {"error": f"Device {device_id} not found"}
            
            period = params.get("period", "7d")
            
            if not self.service.validate_period(period):
                cherrypy.response.status = 400
                return {"error": f"Unsupported period: {period}"}
            
            # Fetch data using service methods
            temp_data = self.service.fetch_sensor_data_from_adaptor(device_id, "temperature", period)
            door_events = self.service.fetch_door_events_from_adaptor(device_id, period)
            
            # Analyze trends using IMPORTED function (not service method)
            trends = analyze_trends(temp_data, door_events, period)
            
            return {
                "device_id": device_id,
                "period": period,
                "trends": trends,
                "generated_at": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            print(f"[REST] Error in trends endpoint: {e}")
            cherrypy.response.status = 500
            return {"error": "Internal server error"}
    
    @cherrypy.expose
    @cherrypy.tools.json_out()
    def patterns(self, device_id, **params):
        """GET /patterns/{device_id}?type={usage|temperature|efficiency}"""
        try:
            # Check if device exists
            if device_id not in self.service.known_devices:
                if not self.service.check_device_exists_in_catalog(device_id):
                    cherrypy.response.status = 404
                    return {"error": f"Device {device_id} not found"}
            
            pattern_type = params.get("type", "usage")
            period = params.get("period", "7d")
            
            if pattern_type not in ["usage", "temperature", "efficiency"]:
                cherrypy.response.status = 400
                return {"error": "Invalid pattern type. Must be: usage, temperature, or efficiency"}
            
            if not self.service.validate_period(period):
                cherrypy.response.status = 400
                return {"error": f"Unsupported period: {period}"}
            
            result = {"device_id": device_id, "type": pattern_type, "period": period}
            
            if pattern_type == "usage":
                door_events = self.service.fetch_door_events_from_adaptor(device_id, period)
                # Call imported function
                result["patterns"] = analyze_door_usage(door_events, period)
                
            elif pattern_type == "temperature":
                temp_data = self.service.fetch_sensor_data_from_adaptor(device_id, "temperature", period)
                # Call imported function
                result["patterns"] = analyze_temperature_data(temp_data, period)
                
            elif pattern_type == "efficiency":
                temp_data = self.service.fetch_sensor_data_from_adaptor(device_id, "temperature", period)
                door_events = self.service.fetch_door_events_from_adaptor(device_id, period)
                
                # Call imported functions
                temp_analysis = analyze_temperature_data(temp_data, period)
                usage_analysis = analyze_door_usage(door_events, period)
                
                combined_efficiency = (temp_analysis["stability_score"] + usage_analysis["efficiency_score"]) / 2
                
                result["patterns"] = {
                    "overall_efficiency": round(combined_efficiency, 1),
                    "temperature_efficiency": temp_analysis["stability_score"],
                    "usage_efficiency": usage_analysis["efficiency_score"],
                    "factors": {
                        "temperature_stability": temp_analysis["stability_score"] > 80,
                        "optimal_door_usage": usage_analysis["efficiency_score"] > 70,
                        "minimal_out_of_range": temp_analysis["out_of_range_time_percent"] < 10
                    }
                }
            
            result["generated_at"] = datetime.now(timezone.utc).isoformat()
            return result
            
        except Exception as e:
            print(f"[REST] Error in patterns endpoint: {e}")
            cherrypy.response.status = 500
            return {"error": "Internal server error"}