import time
import threading
import cherrypy
from datetime import datetime, timezone

from modules.utils import load_settings, save_settings, period_to_days
from modules.catalog_client import CatalogClient
from modules.influx_client import InfluxClient
from modules.analyzer import DataAnalyzer
from modules.rest_api import DataAnalysisRestAPI

class DataAnalysisService:
    def __init__(self, settings_file="settings.json"):
        self.settings_file = settings_file
        self.settings = load_settings(settings_file)
        
        # Service configuration
        self.service_info = self.settings["serviceInfo"]
        self.service_id = self.service_info["serviceID"]
        
        # Initialize modules
        self.catalog_client = CatalogClient(self.settings)
        self.influx_client = InfluxClient(self.settings)
        self.analyzer = DataAnalyzer()
        
        # Device management
        self.known_devices = set()
        
        # REST API
        self.rest_server_thread = None
        
        # Threading
        self.running = True
        self.config_lock = threading.RLock()
        
        print(f"[INIT] {self.service_id} service starting...")

    def save_settings(self):
        """Save current settings to file"""
        save_settings(self.settings, self.settings_file, self.config_lock)

    def validate_period(self, period):
        """Validate if the requested period is supported"""
        supported = self.settings["analysis"]["supported_periods"]
        return period in supported

    def perform_full_analysis(self, device_id, period, metrics_list):
        """Perform complete analysis for a device"""
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
            temp_data = self.influx_client.fetch_sensor_data(device_id, "temperature", period)
            result["temperature_analysis"] = self.analyzer.analyze_temperature(temp_data, period)
        
        if "usage_patterns" in metrics_list:
            door_events = self.influx_client.fetch_door_events(device_id, period)
            result["usage_analysis"] = self.analyzer.analyze_door_usage(door_events, period)
        
        if "trends" in metrics_list:
            if not temp_data:
                temp_data = self.influx_client.fetch_sensor_data(device_id, "temperature", period)
            if not door_events:
                door_events = self.influx_client.fetch_door_events(device_id, period)
            
            result["trends"] = self.analyzer.analyze_trends(temp_data, door_events, period)
        
        # Add data summary
        result["data_summary"] = {
            "temperature_points": len(temp_data),
            "door_events": len(door_events),
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
                self.catalog_client.register_service()

    def get_status(self):
        """Get current service status"""
        return {
            "service_id": self.service_id,
            "status": "running" if self.running else "stopped",
            "rest_api_active": self.rest_server_thread is not None,
            "known_devices": len(self.known_devices),
            "supported_periods": self.settings["analysis"]["supported_periods"],
            "config_version": self.settings.get("configVersion", 1)
        }

    def run(self):
        """Main run method"""
        print("=" * 60)
        print("    SMARTCHILL DATA ANALYSIS SERVICE (MODULAR)")
        print("=" * 60)
        
        # Setup REST API
        print("[INIT] Setting up REST API...")
        if not self.setup_rest_api():
            print("[ERROR] Failed to setup REST API")
            return
        
        # Register with catalog
        print("[INIT] Registering service with catalog...")
        if not self.catalog_client.register_service():
            print("[WARN] Failed to register with catalog - continuing anyway")
        
        # Load known devices from catalog
        print("[INIT] Loading known devices from catalog...")
        self.known_devices = self.catalog_client.load_known_devices()
        
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

if __name__ == "__main__":
    service = DataAnalysisService()
    try:
        service.run()
    except Exception as e:
        print(f"[FATAL] Service error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        service.shutdown()