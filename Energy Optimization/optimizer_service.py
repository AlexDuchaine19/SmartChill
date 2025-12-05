import json
import time
import threading
import requests
import cherrypy
import numpy as np
from datetime import datetime, timezone

# Importiamo la logica pura dal file utils
from optimizer_utils import (
    analyze_temperature_data,
    analyze_door_usage,
    analyze_compressor_cycles,
    estimate_daily_energy_consumption,
    generate_recommendations,
    prepare_and_train_model
)

class EnergyOptimizationService:
    def __init__(self, settings_file="settings.json"):
        self.settings_file = settings_file
        self.settings = self.load_settings()
        
        # Service configuration
        self.service_info = self.settings["serviceInfo"]
        self.service_id = self.service_info["serviceID"]
        self.catalog_url = self.settings["catalog"]["url"]
        self.influx_adaptor_url = self.settings["influxdb_adaptor"]["base_url"]
        
        # ML models storage 
        # {device_id: {"model": model, "features": [...], "last_trained": ts, ...}}
        self.ml_models = {} 
        
        # Device data
        self.known_devices = set()
        self.device_models = {}
        self.models_power_specs = {}
        
        # Threading
        self.running = True
        self.rest_server_thread = None
        self.training_lock = threading.Lock()
        
        print(f"[INIT] {self.service_id} starting with REAL data analysis & Personalized ML...")

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

    def register_with_catalog(self):
        """Register service with catalog"""
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
            response = requests.post(f"{self.catalog_url}/services/register", json=registration_data, timeout=5)
            if response.status_code in [200, 201]:
                print("[REGISTER] Successfully registered with catalog")
                return True
            else:
                print(f"[REGISTER] Failed to register: {response.status_code}")
                return False
        except requests.RequestException as e:
            print(f"[REGISTER] Error: {e}")
            return False

    def load_devices_and_models_from_catalog(self):
        """Load devices and power specifications from catalog"""
        try:
            response = requests.get(f"{self.catalog_url}/devices", timeout=5)
            if response.status_code == 200:
                devices = response.json()
                for device in devices:
                    device_id = device.get("deviceID")
                    model = device.get("model")
                    if device_id and device_id.startswith("SmartChill_") and model:
                        self.known_devices.add(device_id)
                        self.device_models[device_id] = model
                print(f"[INIT] Loaded {len(self.known_devices)} devices")
            
            response = requests.get(f"{self.catalog_url}/models", timeout=5)
            if response.status_code == 200:
                models_data = response.json()
                self.models_power_specs = models_data
                print(f"[INIT] Loaded power specs for {len(models_data)} models")
                return True
            else:
                print("[INIT] No models endpoint, using fallback specs")
                return False
        except requests.RequestException as e:
            print(f"[INIT] Error loading from catalog: {e}")
            return False

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

    # ===================== Data Fetching =====================

    def fetch_historical_temperature(self, device_id, duration="30d"):
        """Fetch historical temperature data from InfluxDB Adaptor."""
        # print(f"[DATA] Fetching historical temperature for {device_id} ({duration})")
        try:
            timeout = self.settings["influxdb_adaptor"]["timeout_seconds"]
            url = f"{self.influx_adaptor_url}/sensors/temperature"
            params = {"last": duration, "device": device_id}
            response = requests.get(url, params=params, timeout=timeout)
            if response.status_code == 200:
                senml_data = response.json()
                data_points = [{"timestamp": e["t"], "value": e["v"]} for e in senml_data.get("e", []) if 't' in e and 'v' in e]
                return data_points
            else:
                print(f"[DATA] Error fetching historical temperature: {response.status_code}")
                return []
        except requests.RequestException as e:
            print(f"[DATA] Error connecting to Adaptor for historical temperature: {e}")
            return []

    def fetch_historical_door_events(self, device_id, duration="30d"):
        """Fetch historical door events from InfluxDB Adaptor."""
        # print(f"[DATA] Fetching historical door events for {device_id} ({duration})")
        try:
            timeout = self.settings["influxdb_adaptor"]["timeout_seconds"]
            url = f"{self.influx_adaptor_url}/events"
            params = {"device": device_id, "last": duration}
            response = requests.get(url, params=params, timeout=timeout)
            if response.status_code == 200:
                events_data = response.json()
                events = events_data.get("events", [])
                return events
            else:
                print(f"[DATA] Error fetching historical door events: {response.status_code}")
                return []
        except requests.RequestException as e:
            print(f"[DATA] Error connecting to Adaptor for historical door events: {e}")
            return []

    # ===================== ML Operations =====================

    def train_runtime_model(self, device_id, training_period="30d"):
        """Train ML model using REAL historical data via Utils."""
        
        with self.training_lock:
            # Check cache
            if device_id in self.ml_models:
                 last_trained_ts = self.ml_models[device_id].get("last_trained", 0)
                 if time.time() - last_trained_ts < 86400: # 24 hours
                      return self.ml_models[device_id]["model"]

            if not self.settings["ml"]["enable_predictions"]: return None
            print(f"[ML] Starting personalized training for {device_id} using data from last {training_period}...")

            # 1. Fetch Data
            hist_temp = self.fetch_historical_temperature(device_id, duration=training_period)
            hist_events = self.fetch_historical_door_events(device_id, duration=training_period)

            # 2. Train using Utils
            min_samples = self.settings['ml']['min_training_samples']
            model, features, mae, r2 = prepare_and_train_model(hist_temp, hist_events, min_samples)

            if not model:
                print(f"[ML] Training failed or insufficient data for {device_id}")
                return None

            # 3. Store model
            self.ml_models[device_id] = {
                "model": model,
                "last_trained": time.time(),
                "features": features,
                "training_days": "var", # Simplified
                "accuracy": {"mae": mae, "r2": r2}
            }
            
            print(f"[ML] Model trained for {device_id} - MAE: {mae:.3f}h, R2: {r2:.3f}")
            return model

    def predict_runtime(self, device_id, future_days=7):
        """Predict daily runtime using the personalized model."""

        # 1. Get or Train Model
        if device_id not in self.ml_models:
             self.train_runtime_model(device_id)
        
        if device_id not in self.ml_models:
            return None # Training failed
            
        model_data = self.ml_models[device_id]
        model = model_data["model"]
        feature_names = model_data["features"]
        
        # 2. Get Recent Context (last 3 days)
        recent_period = "3d"
        temp_data = self.fetch_historical_temperature(device_id, recent_period)
        door_events = self.fetch_historical_door_events(device_id, recent_period)
        
        if not temp_data: return None 
        
        # Analyze current context using Utils
        current_temp_analysis = analyze_temperature_data(temp_data, recent_period)
        current_usage_analysis = analyze_door_usage(door_events, recent_period)

        predictions = []
        power_specs = self.get_device_power_specs(device_id)
        compressor_power = power_specs.get("base_power_watts", 120)

        for day_offset in range(future_days):
            future_timestamp = time.time() + (day_offset * 24 * 3600)
            dt = datetime.fromtimestamp(future_timestamp, tz=timezone.utc)
            
            # Prepare features
            features_for_prediction = {
                "avg_temperature": current_temp_analysis.get("avg_temperature", 4.0),
                "temperature_variance": current_temp_analysis.get("temperature_variance", 0.5),
                "stability_score": current_temp_analysis.get("stability_score", 80),
                "daily_openings": current_usage_analysis.get("avg_daily_openings", 8),
                "avg_door_duration": current_usage_analysis.get("avg_duration_seconds", 30),
                "day_of_week": dt.weekday(), 
            }
            
            # Create vector ordered by feature_names
            try:
                feature_vector = [features_for_prediction.get(name, 0.0) for name in feature_names]
                feature_vector = np.array(feature_vector).reshape(1, -1)
            except Exception: return None

            # Predict
            predicted_runtime = model.predict(feature_vector)[0]
            predicted_runtime = max(2.0, min(20.0, predicted_runtime))
            predicted_kwh = (predicted_runtime * compressor_power) / 1000
            
            predictions.append({
                "day": day_offset + 1,
                "date": dt.strftime("%Y-%m-%d"),
                "predicted_runtime_hours": round(predicted_runtime, 2),
                "predicted_kwh": round(predicted_kwh, 3),
                "timestamp": int(future_timestamp) 
            })
            
        return predictions

    # ===================== Main Analysis =====================

    def analyze_device_energy(self, device_id, period="7d"):
        """Main analysis function using REAL data & PERSONALIZED predictions."""
        print(f"[ANALYSIS] Analyzing {device_id} (Period: {period})")

        power_specs = self.get_device_power_specs(device_id)
        temp_data = self.fetch_historical_temperature(device_id, period)
        door_events = self.fetch_historical_door_events(device_id, period)

        # Use imported Utils for calculations
        temp_analysis = analyze_temperature_data(temp_data, period)
        usage_analysis = analyze_door_usage(door_events, period)
        cycle_analysis = analyze_compressor_cycles(temp_data, power_specs, period_info=period)

        energy_estimate = estimate_daily_energy_consumption(
            temp_analysis, usage_analysis, cycle_analysis, power_specs
        )

        predictions = None
        if self.settings["ml"]["enable_predictions"]:
            predictions = self.predict_runtime(device_id, future_days=7)

        recommendations = generate_recommendations(
            temp_analysis, usage_analysis, energy_estimate, power_specs
        )

        return {
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

    # ===================== Status & Infrastructure =====================

    def setup_rest_api(self):
        try:
            cherrypy.config.update({'server.socket_host': '0.0.0.0', 'server.socket_port': 8003, 'engine.autoreload.on': False, 'log.screen': False})
            cherrypy.tree.mount(EnergyOptimizationRestAPI(self), '/', {'/': {'tools.response_headers.on': True, 'tools.response_headers.headers': [('Content-Type', 'application/json')]}})
            def start_server(): cherrypy.engine.start(); print("[REST] Server started on port 8003")
            self.rest_server_thread = threading.Thread(target=start_server, daemon=True); self.rest_server_thread.start(); time.sleep(2)
            return True
        except Exception as e: print(f"[REST] Failed to start server: {e}"); return False

    def get_status(self):
        model_info = {dev: {"last_trained": time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(data.get("last_trained", 0)))} for dev, data in self.ml_models.items()}
        return {
            "service_id": self.service_id, "status": "running" if self.running else "stopped",
            "analysis_method": "real_duty_cycle_personalized_prediction", "known_devices": len(self.known_devices),
            "trained_personalized_models": len(self.ml_models), "ml_enabled": self.settings["ml"]["enable_predictions"],
            "model_details": model_info
        }

    def run(self):
        print("="*60 + "\n    SMARTCHILL ENERGY OPTIMIZATION SERVICE\n" + "="*60)
        if not self.setup_rest_api(): print("[ERROR] Failed to setup REST API"); return
        if not self.register_with_catalog(): print("[WARN] Failed to register with catalog")
        self.load_devices_and_models_from_catalog()
        print(f"[INIT] Service started successfully! REST API: http://localhost:8003")
        
        try:
            while self.running: time.sleep(1)
        except KeyboardInterrupt: print("\n[SHUTDOWN] Received interrupt signal..."); self.shutdown()

    def shutdown(self):
        print("[SHUTDOWN] Stopping service..."); self.running = False
        if self.rest_server_thread:
            try: cherrypy.engine.exit(); print("[SHUTDOWN] REST API stopped")
            except Exception as e: print(f"[SHUTDOWN] Error stopping REST API: {e}")

class EnergyOptimizationRestAPI:
    def __init__(self, service): self.service = service

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def health(self): return {"status": "healthy", "service": "Energy Optimization", "timestamp": datetime.now(timezone.utc).isoformat()}

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def status(self): return self.service.get_status()

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def optimize(self, device_id, period="7d"):
        try:
            if device_id not in self.service.known_devices: cherrypy.response.status = 404; return {"error": f"Device {device_id} not found"}
            result = self.service.analyze_device_energy(device_id, period)
            if not result: cherrypy.response.status = 500; return {"error": "Analysis failed"}
            return result
        except Exception as e: cherrypy.response.status = 500; return {"error": "Internal server error", "details": str(e)}

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def predictions(self, device_id):
        try:
            if device_id not in self.service.known_devices: cherrypy.response.status = 404; return {"error": f"Device {device_id} not found"}
            if not self.service.settings["ml"]["enable_predictions"]: cherrypy.response.status = 503; return {"error": "ML predictions disabled"}
            
            predictions = self.service.predict_runtime(device_id, future_days=7)
            if predictions is None:
                cherrypy.response.status = 500
                return {"error": f"Failed to generate predictions for {device_id}"}
            
            return {
                "device_id": device_id, "predictions": predictions,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        except Exception as e: cherrypy.response.status = 500; return {"error": "Internal server error", "details": str(e)}

    @cherrypy.expose  
    @cherrypy.tools.json_out()
    def runtime(self, device_id, period="7d"):
        try:
            if device_id not in self.service.known_devices: cherrypy.response.status = 404; return {"error": f"Device {device_id} not found"}
            
            result = self.service.analyze_device_energy(device_id, period=period)
            if not result: cherrypy.response.status = 500; return {"error": "Analysis failed"}
                
            power_specs = self.service.get_device_power_specs(device_id)
            return {
                "device_id": device_id,
                "cycle_analysis": result.get("current_energy", {}).get("cycle_analysis", {}),
                "energy_breakdown": result.get("current_energy", {}),
                "power_specs_used": power_specs,
                "period_analyzed": period
            }
        except Exception as e: cherrypy.response.status = 500; return {"error": "Internal server error"}