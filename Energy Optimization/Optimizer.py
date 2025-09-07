import json
import time
import threading
import requests
import random
import numpy as np
import cherrypy
from datetime import datetime, timezone
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, r2_score
import warnings
warnings.filterwarnings('ignore')

class EnergyOptimizationService:
    def __init__(self, settings_file="settings.json"):
        self.settings_file = settings_file
        self.settings = self.load_settings()
        
        # Service configuration
        self.service_info = self.settings["serviceInfo"]
        self.service_id = self.service_info["serviceID"]
        self.catalog_url = self.settings["catalog"]["url"]
        self.data_analysis_url = self.settings["data_analysis"]["base_url"]
        
        # ML models storage
        self.ml_models = {}  # {device_id: model_data}
        
        # Cache for optimization results
        self.optimization_cache = {}
        self.cache_lock = threading.RLock()
        
        # Device data loaded from catalog
        self.known_devices = set()
        self.device_models = {}  # {device_id: model_name}
        self.models_power_specs = {}  # {model_name: power_specs}
        
        # Threading
        self.running = True
        self.rest_server_thread = None
        
        print(f"[INIT] {self.service_id} starting...")

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
            
            response = requests.post(
                f"{self.catalog_url}/services/register",
                json=registration_data,
                timeout=5
            )
            
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
            # Load devices
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
            
            # Load device models with power specs
            response = requests.get(f"{self.catalog_url}/models", timeout=5)
            if response.status_code == 200:
                models_data = response.json()
                self.models_power_specs = models_data
                print(f"[INIT] Loaded power specs for {len(models_data)} models")
                return True
            else:
                print(f"[INIT] No models endpoint, using fallback specs")
                return False
                
        except requests.RequestException as e:
            print(f"[INIT] Error loading from catalog: {e}")
            return False

    def get_device_power_specs(self, device_id):
        """Get power specifications for a device"""
        if device_id not in self.device_models:
            return self.settings["defaults"]["fallback_power_specs"]
        
        model = self.device_models[device_id]
        
        if model in self.models_power_specs:
            model_specs = self.models_power_specs[model]
            power_specs = model_specs.get("power_consumption", {})
            # Merge with defaults for missing values
            default_specs = self.settings["defaults"]["fallback_power_specs"]
            return {**default_specs, **power_specs}
        else:
            return self.settings["defaults"]["fallback_power_specs"]

    def get_cache_key(self, device_id, analysis_type="full"):
        """Generate cache key"""
        cache_window = int(time.time() // (self.settings["cache"]["duration_minutes"] * 60))
        return f"{device_id}_{analysis_type}_{cache_window}"

    def get_cached_result(self, cache_key):
        """Get cached result if valid"""
        with self.cache_lock:
            if cache_key in self.optimization_cache:
                cached_data, timestamp = self.optimization_cache[cache_key]
                cache_duration = self.settings["cache"]["duration_minutes"] * 60
                
                if time.time() - timestamp < cache_duration:
                    cached_data["cached"] = True
                    return cached_data
                else:
                    del self.optimization_cache[cache_key]
            return None

    def store_cached_result(self, cache_key, result):
        """Store result in cache"""
        with self.cache_lock:
            self.optimization_cache[cache_key] = (result, time.time())
            
            # Limit cache size
            if len(self.optimization_cache) > self.settings["cache"]["max_entries"]:
                oldest_key = min(self.optimization_cache.keys(), 
                               key=lambda k: self.optimization_cache[k][1])
                del self.optimization_cache[oldest_key]

    def fetch_analysis_data(self, device_id, period="7d"):
        """Fetch data from Data Analysis Service"""
        try:
            url = f"{self.data_analysis_url}/analyze/{device_id}"
            params = {
                "period": period,
                "metrics": "temperature,usage_patterns,trends"
            }
            
            response = requests.get(url, params=params, 
                                  timeout=self.settings["data_analysis"]["timeout_seconds"])
            
            if response.status_code == 200:
                return response.json()
            else:
                print(f"[DATA] Error fetching analysis: {response.status_code}")
                return None
                
        except requests.RequestException as e:
            print(f"[DATA] Error connecting to Data Analysis Service: {e}")
            return None

    def extract_features_from_analysis(self, analysis_data):
        """Extract ML features from analysis data"""
        if not analysis_data:
            return None
        
        try:
            temp_analysis = analysis_data.get("temperature_analysis", {})
            usage_analysis = analysis_data.get("usage_analysis", {})
            trends = analysis_data.get("trends", {})
            
            # Calculate time features
            dt = datetime.now(timezone.utc)
            hour_factor = 0.5 * (1 + np.cos(2 * np.pi * (dt.hour - 12) / 24))
            day_factor = 0.7 if dt.weekday() < 5 else 1.0
            
            features = {
                "avg_temperature": temp_analysis.get("avg_temperature", 4.0),
                "temperature_variance": temp_analysis.get("temperature_variance", 0.5),
                "temp_stability_score": temp_analysis.get("stability_score", 80.0),
                "door_openings_per_day": usage_analysis.get("avg_daily_openings", 5.0),
                "avg_door_duration": usage_analysis.get("avg_duration_seconds", 30.0),
                "usage_efficiency_score": usage_analysis.get("efficiency_score", 75.0),
                "hour_factor": hour_factor,
                "day_factor": day_factor
            }
            
            # Convert trend strings to numbers
            temp_trend = trends.get("temperature_trend", "stable")
            usage_trend = trends.get("usage_trend", "stable")
            
            trend_map = {"increasing": 1.2, "decreasing": 0.8, "stable": 1.0}
            features["temperature_trend_num"] = trend_map.get(temp_trend, 1.0)
            features["usage_trend_num"] = trend_map.get(usage_trend, 1.0)
            
            return features
            
        except Exception as e:
            print(f"[FEATURES] Error extracting features: {e}")
            return None

    def calculate_efficiency_factor(self, device_id, avg_temp):
        """Calculate efficiency factor based on temperature"""
        power_specs = self.get_device_power_specs(device_id)
        optimal_range = power_specs["optimal_temp_range"]
        penalty_per_degree = power_specs["temp_penalty_per_degree"]
        
        if avg_temp < optimal_range[0]:
            deviation = optimal_range[0] - avg_temp
        elif avg_temp > optimal_range[1]:
            deviation = avg_temp - optimal_range[1]
        else:
            deviation = 0
        
        efficiency_factor = max(0.5, 1.0 - (deviation * penalty_per_degree))
        return efficiency_factor

    def estimate_power_consumption(self, device_id, features):
        """Estimate power consumption based on features"""
        power_specs = self.get_device_power_specs(device_id)
        
        # Base power
        base_power = power_specs["base_power_watts"]
        
        # Door penalty
        daily_openings = features["door_openings_per_day"]
        avg_duration = features["avg_door_duration"]
        door_penalty_watts = power_specs["door_penalty_watts"]
        
        daily_open_hours = (daily_openings * avg_duration) / 3600
        door_penalty = daily_open_hours * door_penalty_watts
        
        # Temperature penalty
        temp_penalty = features["temperature_variance"] * power_specs["temp_variance_penalty"] * base_power
        
        # Efficiency factor
        efficiency_factor = self.calculate_efficiency_factor(device_id, features["avg_temperature"])
        
        # Trend factor
        trend_factor = features["temperature_trend_num"] * features["usage_trend_num"]
        
        # Total calculation
        estimated_watts = (base_power + door_penalty + temp_penalty) * trend_factor / efficiency_factor
        daily_kwh = (estimated_watts * 24) / 1000
        
        return {
            "estimated_watts": round(estimated_watts, 2),
            "daily_kwh": round(daily_kwh, 3),
            "base_power": base_power,
            "door_penalty": round(door_penalty, 2),
            "temp_penalty": round(temp_penalty, 2),
            "efficiency_factor": round(efficiency_factor, 3),
            "trend_factor": round(trend_factor, 3)
        }

    def generate_recommendations(self, device_id, analysis_data, energy_estimate):
        """Generate optimization recommendations"""
        recommendations = []
        power_specs = self.get_device_power_specs(device_id)
        
        usage_analysis = analysis_data.get("usage_analysis", {})
        temp_analysis = analysis_data.get("temperature_analysis", {})
        
        # Door usage recommendations
        daily_openings = usage_analysis.get("avg_daily_openings", 0)
        max_efficient = power_specs.get("max_efficient_openings_per_day", 15)
        
        if daily_openings > max_efficient:
            potential_savings = (daily_openings - max_efficient) * 0.02
            recommendations.append({
                "type": "behavioral",
                "priority": "high",
                "message": f"Reduce door openings: {daily_openings:.1f}/day. Target: <{max_efficient}/day",
                "potential_savings_kwh": round(potential_savings, 2),
                "potential_savings_percent": round((potential_savings / energy_estimate['daily_kwh']) * 100, 1)
            })
        
        # Temperature recommendations
        avg_temp = temp_analysis.get("avg_temperature", 4.0)
        optimal_range = power_specs["optimal_temp_range"]
        
        if avg_temp < optimal_range[0] - 1:
            recommendations.append({
                "type": "setting",
                "priority": "medium",
                "message": f"Temperature too low: {avg_temp:.1f}°C. Raise to {optimal_range[0]}°C",
                "potential_savings_kwh": 0.4,
                "potential_savings_percent": 10
            })
        elif avg_temp > optimal_range[1] + 1:
            recommendations.append({
                "type": "setting",
                "priority": "high",
                "message": f"Temperature too high: {avg_temp:.1f}°C. Lower to {optimal_range[1]}°C",
                "potential_savings_kwh": -0.1,
                "potential_savings_percent": 0
            })
        
        return recommendations

    def train_ml_model(self, device_id):
        """Train ML model for predictions"""
        if not self.settings["ml"]["enable_predictions"]:
            return None
        
        # Generate training data
        training_data = self.generate_training_data(device_id, samples=100)
        
        if len(training_data) < self.settings["ml"]["min_training_samples"]:
            print(f"[ML] Insufficient training data for {device_id}")
            return None
        
        feature_names = self.settings["ml"]["features"]
        X, y = [], []
        
        for sample in training_data:
            feature_vector = [sample["features"].get(fname, 0) for fname in feature_names]
            X.append(feature_vector)
            y.append(sample["energy_kwh"])
        
        X, y = np.array(X), np.array(y)
        
        # Train model
        model = LinearRegression()
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
        model.fit(X_train, y_train)
        
        # Evaluate
        y_pred = model.predict(X_test)
        mae = mean_absolute_error(y_test, y_pred)
        r2 = r2_score(y_test, y_pred)
        
        # Store model
        self.ml_models[device_id] = {
            "model": model,
            "last_trained": time.time(),
            "features": feature_names,
            "accuracy": {"mae": mae, "r2": r2}
        }
        
        print(f"[ML] Model trained for {device_id} - MAE: {mae:.3f}, R2: {r2:.3f}")
        return model

    def generate_training_data(self, device_id, samples=100):
        """Generate synthetic training data"""
        training_data = []
        
        # Try to get real patterns first
        analysis_data = self.fetch_analysis_data(device_id, period="30d")
        base_features = self.extract_features_from_analysis(analysis_data) if analysis_data else None
        
        for i in range(samples):
            if base_features:
                # Vary around real patterns
                features = base_features.copy()
                features["door_openings_per_day"] *= random.uniform(0.7, 1.3)
                features["avg_door_duration"] *= random.uniform(0.8, 1.2)
                features["avg_temperature"] += random.uniform(-0.5, 0.5)
                features["temperature_variance"] *= random.uniform(0.5, 1.5)
            else:
                # Generate synthetic features
                features = {
                    "avg_temperature": random.uniform(2, 8),
                    "temperature_variance": random.uniform(0.1, 2.0),
                    "temp_stability_score": random.uniform(60, 95),
                    "door_openings_per_day": random.uniform(3, 15),
                    "avg_door_duration": random.uniform(15, 90),
                    "usage_efficiency_score": random.uniform(60, 90),
                    "temperature_trend_num": random.uniform(0.8, 1.2),
                    "usage_trend_num": random.uniform(0.7, 1.3),
                    "hour_factor": random.uniform(0.3, 1.0),
                    "day_factor": random.uniform(0.7, 1.0)
                }
            
            energy_estimate = self.estimate_power_consumption(device_id, features)
            energy_kwh = energy_estimate["daily_kwh"] + random.uniform(-0.2, 0.2)
            
            training_data.append({
                "features": features,
                "energy_kwh": max(0.5, energy_kwh)
            })
        
        return training_data

    def predict_energy_consumption(self, device_id, future_days=7):
        """Generate energy predictions"""
        if device_id not in self.ml_models:
            model = self.train_ml_model(device_id)
            if not model:
                return None
        
        model_data = self.ml_models[device_id]
        model = model_data["model"]
        feature_names = model_data["features"]
        
        # Get current patterns
        analysis_data = self.fetch_analysis_data(device_id, period="7d")
        base_features = self.extract_features_from_analysis(analysis_data)
        
        if not base_features:
            return None
        
        predictions = []
        for day in range(future_days):
            future_timestamp = time.time() + (day * 24 * 3600)
            
            # Update time-based features for future day
            dt = datetime.fromtimestamp(future_timestamp, tz=timezone.utc)
            hour_factor = 0.5 * (1 + np.cos(2 * np.pi * (dt.hour - 12) / 24))
            day_factor = 0.7 if dt.weekday() < 5 else 1.0
            
            prediction_features = base_features.copy()
            prediction_features["hour_factor"] = hour_factor
            prediction_features["day_factor"] = day_factor
            
            feature_vector = [prediction_features.get(fname, 0) for fname in feature_names]
            feature_vector = np.array(feature_vector).reshape(1, -1)
            
            predicted_kwh = model.predict(feature_vector)[0]
            
            predictions.append({
                "day": day + 1,
                "date": dt.strftime("%Y-%m-%d"),
                "predicted_kwh": round(predicted_kwh, 3),
                "timestamp": int(future_timestamp)
            })
        
        return predictions

    def analyze_device_energy(self, device_id):
        """Main energy analysis function"""
        print(f"[ANALYSIS] Analyzing {device_id}")
        
        # Check cache
        cache_key = self.get_cache_key(device_id, "full")
        cached_result = self.get_cached_result(cache_key)
        if cached_result:
            print(f"[ANALYSIS] Returning cached result for {device_id}")
            return cached_result
        
        # Fetch analysis data
        analysis_data = self.fetch_analysis_data(device_id, period="7d")
        if not analysis_data:
            print(f"[ANALYSIS] No data available for {device_id}")
            return None
        
        # Extract features
        features = self.extract_features_from_analysis(analysis_data)
        if not features:
            print(f"[ANALYSIS] Could not extract features for {device_id}")
            return None
        
        # Calculate energy estimate
        energy_estimate = self.estimate_power_consumption(device_id, features)
        
        # Generate predictions if enabled
        predictions = None
        if self.settings["ml"]["enable_predictions"]:
            predictions = self.predict_energy_consumption(device_id, future_days=7)
        
        # Generate recommendations
        recommendations = self.generate_recommendations(device_id, analysis_data, energy_estimate)
        
        # Compile result
        result = {
            "device_id": device_id,
            "model": self.device_models.get(device_id, "unknown"),
            "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
            "data_source": "data_analysis_service",
            "current_energy": energy_estimate,
            "ml_features": features,
            "predictions": predictions,
            "recommendations": recommendations,
            "service": self.service_id,
            "cached": False
        }
        
        # Store in cache
        self.store_cached_result(cache_key, result)
        
        print(f"[ANALYSIS] Completed analysis for {device_id}")
        return result

    def setup_rest_api(self):
        """Setup REST API"""
        try:
            cherrypy.config.update({
                'server.socket_host': '0.0.0.0',
                'server.socket_port': 8003,
                'engine.autoreload.on': False,
                'log.screen': False
            })
            
            cherrypy.tree.mount(EnergyOptimizationRestAPI(self), '/', {
                '/': {
                    'tools.response_headers.on': True,
                    'tools.response_headers.headers': [('Content-Type', 'application/json')],
                }
            })
            
            def start_server():
                cherrypy.engine.start()
                print("[REST] Server started on port 8003")
            
            self.rest_server_thread = threading.Thread(target=start_server, daemon=True)
            self.rest_server_thread.start()
            time.sleep(2)
            
            return True
            
        except Exception as e:
            print(f"[REST] Failed to start server: {e}")
            return False

    def periodic_registration(self):
        """Periodic catalog registration"""
        interval = self.settings["catalog"]["registration_interval_seconds"]
        
        while self.running:
            time.sleep(interval)
            if self.running:
                self.register_with_catalog()

    def get_status(self):
        """Get service status"""
        return {
            "service_id": self.service_id,
            "status": "running" if self.running else "stopped",
            "known_devices": len(self.known_devices),
            "trained_models": len(self.ml_models),
            "cached_results": len(self.optimization_cache),
            "data_analysis_service": self.data_analysis_url,
            "ml_enabled": self.settings["ml"]["enable_predictions"],
            "config_version": self.settings.get("configVersion", 1)
        }

    def run(self):
        """Main run method"""
        print("=" * 60)
        print("    SMARTCHILL ENERGY OPTIMIZATION SERVICE")
        print("    MODE: REST-ONLY (ON-DEMAND)")
        print("=" * 60)
        
        # Setup REST API
        if not self.setup_rest_api():
            print("[ERROR] Failed to setup REST API")
            return
        
        # Register with catalog
        if not self.register_with_catalog():
            print("[WARN] Failed to register with catalog")
        
        # Load devices and models
        self.load_devices_and_models_from_catalog()
        
        print(f"[INIT] Service started successfully!")
        print(f"[INIT] Data Analysis Service: {self.data_analysis_url}")
        print(f"[INIT] Known devices: {len(self.known_devices)}")
        print(f"[INIT] REST API: http://localhost:8003")
        print(f"[INIT] Cache duration: {self.settings['cache']['duration_minutes']} minutes")
        
        # Start background registration thread
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
        print("[SHUTDOWN] Stopping service...")
        self.running = False
        
        if self.rest_server_thread:
            try:
                cherrypy.engine.exit()
                print("[SHUTDOWN] REST API stopped")
            except Exception as e:
                print(f"[SHUTDOWN] Error stopping REST API: {e}")
        
        print("[SHUTDOWN] Service stopped")


class EnergyOptimizationRestAPI:
    """REST API endpoints"""
    
    def __init__(self, service):
        self.service = service

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def health(self):
        """Health check endpoint"""
        try:
            return {
                "status": "healthy",
                "service": "Energy Optimization",
                "mode": "REST-only",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "known_devices": len(self.service.known_devices),
                "ml_models": len(self.service.ml_models)
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
        """Detailed status endpoint"""
        return self.service.get_status()

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def optimize(self, device_id):
        """Optimization analysis endpoint"""
        try:
            if device_id not in self.service.known_devices:
                cherrypy.response.status = 404
                return {
                    "error": f"Device {device_id} not found",
                    "known_devices": list(self.service.known_devices)
                }
            
            result = self.service.analyze_device_energy(device_id)
            
            if not result:
                cherrypy.response.status = 500
                return {
                    "error": f"Failed to analyze device {device_id}",
                    "reason": "Data Analysis Service unavailable or insufficient data"
                }
            
            return result
            
        except Exception as e:
            print(f"[REST] Error in optimize endpoint: {e}")
            cherrypy.response.status = 500
            return {
                "error": "Internal server error",
                "details": str(e)
            }

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def predictions(self, device_id):
        """Predictions endpoint"""
        try:
            if device_id not in self.service.known_devices:
                cherrypy.response.status = 404
                return {
                    "error": f"Device {device_id} not found",
                    "known_devices": list(self.service.known_devices)
                }
            
            if not self.service.settings["ml"]["enable_predictions"]:
                cherrypy.response.status = 503
                return {
                    "error": "ML predictions disabled",
                    "device_id": device_id
                }
            
            predictions = self.service.predict_energy_consumption(device_id, future_days=7)
            
            if not predictions:
                cherrypy.response.status = 500
                return {
                    "error": f"Failed to generate predictions for {device_id}",
                    "reason": "Model not available or insufficient data"
                }
            
            # Get model info
            model_info = {}
            if device_id in self.service.ml_models:
                model_data = self.service.ml_models[device_id]
                model_info = {
                    "last_trained": datetime.fromtimestamp(model_data["last_trained"], tz=timezone.utc).isoformat(),
                    "accuracy": model_data.get("accuracy", {})
                }
            
            return {
                "device_id": device_id,
                "model": self.service.device_models.get(device_id, "unknown"),
                "predictions": predictions,
                "model_info": model_info,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            print(f"[REST] Error in predictions endpoint: {e}")
            cherrypy.response.status = 500
            return {
                "error": "Internal server error",
                "details": str(e)
            }

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def devices(self):
        """List all devices with their specs"""
        try:
            devices_info = []
            for device_id in self.service.known_devices:
                model = self.service.device_models.get(device_id, "unknown")
                power_specs = self.service.get_device_power_specs(device_id)
                
                devices_info.append({
                    "device_id": device_id,
                    "model": model,
                    "power_specs": power_specs,
                    "ml_model_trained": device_id in self.service.ml_models
                })
            
            return {
                "devices": devices_info,
                "total_devices": len(self.service.known_devices),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            print(f"[REST] Error in devices endpoint: {e}")
            cherrypy.response.status = 500
            return {
                "error": "Internal server error",
                "details": str(e)
            }

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def models(self):
        """List all device models with power specs"""
        try:
            return {
                "models": self.service.models_power_specs,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            print(f"[REST] Error in models endpoint: {e}")
            cherrypy.response.status = 500
            return {
                "error": "Internal server error",
                "details": str(e)
            }


def main():
    """Main entry point"""
    service = EnergyOptimizationService()
    
    try:
        service.run()
    except Exception as e:
        print(f"[FATAL] Service error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        service.shutdown()


if __name__ == "__main__":
    main()