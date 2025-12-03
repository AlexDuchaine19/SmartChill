import time
import threading
import cherrypy
from datetime import datetime, timezone

from modules.utils import load_settings
from modules.catalog_client import CatalogClient
from modules.influx_client import InfluxClient
from modules.analyzer import EnergyAnalyzer
from modules.ml_engine import MLEngine
from modules.rest_api import EnergyOptimizationRestAPI

class EnergyOptimizationService:
    def __init__(self, settings_file="settings.json"):
        self.settings = load_settings(settings_file)
        
        # Service configuration
        self.service_info = self.settings["serviceInfo"]
        self.service_id = self.service_info["serviceID"]
        
        # Initialize modules
        self.catalog_client = CatalogClient(self.settings)
        self.influx_client = InfluxClient(self.settings)
        self.analyzer = EnergyAnalyzer()
        self.ml_engine = MLEngine(self.settings, self.analyzer)
        
        # Device data
        self.known_devices = set()
        self.device_models = {}
        self.models_power_specs = {}
        
        # Threading
        self.running = True
        self.rest_server_thread = None
        
        print(f"[INIT] {self.service_id} starting with REAL data analysis & Personalized ML...")

    def load_devices_and_models_from_catalog(self):
        """Load devices and power specifications from catalog"""
        devices = self.catalog_client.get_devices()
        for device in devices:
            device_id = device.get("deviceID")
            model = device.get("model")
            if device_id and device_id.startswith("SmartChill_") and model:
                self.known_devices.add(device_id)
                self.device_models[device_id] = model
        print(f"[INIT] Loaded {len(self.known_devices)} devices")
        
        self.models_power_specs = self.catalog_client.get_models()
        print(f"[INIT] Loaded power specs for {len(self.models_power_specs)} models")

    def get_device_power_specs(self, device_id):
        """Get power specifications for a device, merging with defaults"""
        fallback = self.settings["defaults"]["fallback_power_specs"]
        if device_id not in self.device_models: return fallback
        model = self.device_models[device_id]
        if model in self.models_power_specs:
            model_specs = self.models_power_specs[model]
            power_specs = model_specs.get("power_consumption", {})
            return {**fallback, **power_specs}
        else:
            return fallback

    def analyze_device_energy(self, device_id, period="7d"):
        """Main analysis function using REAL data & PERSONALIZED predictions."""
        print(f"[ANALYSIS] Analyzing {device_id} with REAL data model (Period: {period})")

        power_specs = self.get_device_power_specs(device_id)

        temp_data = self.influx_client.fetch_historical_temperature(device_id, period)
        door_events = self.influx_client.fetch_historical_door_events(device_id, period)

        temp_analysis = self.analyzer.analyze_temperature_data(temp_data, period)
        usage_analysis = self.analyzer.analyze_door_usage(door_events, period)
        cycle_analysis = self.analyzer.analyze_compressor_cycles(temp_data, power_specs, period_info=period)

        energy_estimate = self.analyzer.estimate_daily_energy_consumption(
            device_id, temp_analysis, usage_analysis, cycle_analysis, power_specs
        )

        predictions = None
        if self.settings["ml"]["enable_predictions"]:
            # Train model if needed (using longer history)
            hist_period = self.settings["ml"].get("training_period", "30d")
            hist_temp = self.influx_client.fetch_historical_temperature(device_id, hist_period)
            hist_events = self.influx_client.fetch_historical_door_events(device_id, hist_period)
            
            self.ml_engine.train_runtime_model(device_id, hist_temp, hist_events)
            
            compressor_power = power_specs.get("base_power_watts", 120)
            predictions = self.ml_engine.predict_runtime(
                device_id, temp_analysis, usage_analysis, compressor_power, future_days=7
            )

        recommendations = self.analyzer.generate_recommendations(
            device_id, temp_analysis, usage_analysis, energy_estimate, power_specs
        )

        result = {
            "device_id": device_id,
            "model": self.device_models.get(device_id, "unknown"),
            "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
            "analysis_method": "real_duty_cycle_personalized_prediction",
            "current_energy": energy_estimate,
            "statistics": {
                "temperature": temp_analysis,
                "usage": usage_analysis
            },
            "predictions": predictions,
            "recommendations": recommendations,
            "service": self.service_id
        }

        print(f"[ANALYSIS] Real data analysis completed for {device_id}: {energy_estimate['daily_kwh']} kWh/day")
        return result

    def setup_rest_api(self):
        try:
            cherrypy.config.update({'server.socket_host': '0.0.0.0', 'server.socket_port': 8003, 'engine.autoreload.on': False, 'log.screen': False})
            cherrypy.tree.mount(EnergyOptimizationRestAPI(self), '/', {'/': {'tools.response_headers.on': True, 'tools.response_headers.headers': [('Content-Type', 'application/json')]}})
            def start_server(): cherrypy.engine.start(); print("[REST] Server started on port 8003")
            self.rest_server_thread = threading.Thread(target=start_server, daemon=True); self.rest_server_thread.start(); time.sleep(2)
            return True
        except Exception as e: print(f"[REST] Failed to start server: {e}"); return False

    def get_status(self):
        model_info = {dev: {"trained_days": data.get("training_days", "N/A"), "last_trained": time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(data.get("last_trained", 0)))} for dev, data in self.ml_engine.ml_models.items()}
        return {
            "service_id": self.service_id, "status": "running" if self.running else "stopped",
            "analysis_method": "real_duty_cycle_personalized_prediction", "known_devices": len(self.known_devices),
            "trained_personalized_models": len(self.ml_engine.ml_models), "ml_enabled": self.settings["ml"]["enable_predictions"],
            "adaptor_url": self.influx_client.base_url, "model_details": model_info
        }

    def run(self):
        print("="*60 + "\n    SMARTCHILL ENERGY OPTIMIZATION SERVICE (MODULAR)\n" + "="*60)
        if not self.setup_rest_api(): print("[ERROR] Failed to setup REST API"); return
        if not self.catalog_client.register_service(): print("[WARN] Failed to register with catalog")
        self.load_devices_and_models_from_catalog()
        
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[SHUTDOWN] Stopping service...")
            self.running = False
            cherrypy.engine.exit()

if __name__ == "__main__":
    service = EnergyOptimizationService()
    service.run()