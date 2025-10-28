import json
import time
import threading
import requests
import random
import numpy as np
import cherrypy
from datetime import datetime, timezone, timedelta
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import train_test_split # Keep for potential future use
from sklearn.metrics import mean_absolute_error, r2_score
from collections import defaultdict
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
        self.influx_adaptor_url = self.settings["influxdb_adaptor"]["base_url"]
        
        # ML models storage (now personalized)
        self.ml_models = {} # {device_id: {"model": model, "features": [...], ...}}
        
        # Device data
        self.known_devices = set()
        self.device_models = {}
        self.models_power_specs = {}
        
        # Threading
        self.running = True
        self.rest_server_thread = None
        self.training_lock = threading.Lock() # Lock to prevent simultaneous training for same device
        
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
                "version": self.service_info["version"], # Consider bumping version
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
            return {**fallback, **power_specs} # Merge model specs over fallback
        else:
            return fallback

    ## ----------------------------------------
    ## NEW HELPER FUNCTIONS FOR HISTORICAL DATA
    ## ----------------------------------------

    def fetch_historical_temperature(self, device_id, duration="30d"):
        """Fetch historical temperature data from InfluxDB Adaptor."""
        print(f"[DATA] Fetching historical temperature for {device_id} ({duration})")
        try:
            timeout = self.settings["influxdb_adaptor"]["timeout_seconds"]
            url = f"{self.influx_adaptor_url}/sensors/temperature"
            params = {"last": duration, "device": device_id}
            response = requests.get(url, params=params, timeout=timeout)
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
            timeout = self.settings["influxdb_adaptor"]["timeout_seconds"]
            url = f"{self.influx_adaptor_url}/events"
            params = {"device": device_id, "last": duration}
            response = requests.get(url, params=params, timeout=timeout)
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

    def calculate_historical_runtime(self, daily_temp_data):
        """Calculate the actual duty cycle/runtime for a given day's temperature data."""
        if len(daily_temp_data) < 20: # Need ~2 hours of data points minimum for a daily estimate
            return {"duty_cycle": None, "runtime_hours": None}

        # Use a simplified version of analyze_compressor_cycles logic
        temperatures = [p['value'] for p in daily_temp_data]
        timestamps = [p['timestamp'] for p in daily_temp_data]
        
        cycles = []
        current_trend = "unknown"
        cycle_start_index = 0
        temp_threshold = 0.1 # Sensitivity for trend change

        for i in range(1, len(temperatures)):
            temp_change = temperatures[i] - temperatures[i-1]
            time_diff_minutes = (timestamps[i] - timestamps[i-1]) / 60.0

            if time_diff_minutes <= 0 or time_diff_minutes > 60: continue # Skip large gaps or invalid data

            if temp_change < -temp_threshold / time_diff_minutes and current_trend != "cooling": # Normalize threshold by time diff
                if current_trend == "warming":
                    cycle_duration = (timestamps[i] - timestamps[cycle_start_index]) / 60
                    if cycle_duration < 120: cycles.append({"type": "off", "duration": cycle_duration}) # Filter out too long cycles
                current_trend = "cooling"
                cycle_start_index = i
                
            elif temp_change > temp_threshold / time_diff_minutes and current_trend != "warming":
                if current_trend == "cooling":
                    cycle_duration = (timestamps[i] - timestamps[cycle_start_index]) / 60
                    if cycle_duration < 120: cycles.append({"type": "on", "duration": cycle_duration}) # Filter out too long cycles
                current_trend = "warming"
                cycle_start_index = i
        
        if not cycles:
            # If no clear cycles, estimate based on time below average (crude fallback)
            avg_temp = np.mean(temperatures)
            time_below_avg = sum((timestamps[i] - timestamps[i-1]) / 60.0 
                                 for i in range(1, len(timestamps)) if temperatures[i] < avg_temp)
            total_time = (timestamps[-1] - timestamps[0]) / 60.0
            duty_cycle = time_below_avg / total_time if total_time > 0 else 0.4
            duty_cycle = max(0.1, min(0.9, duty_cycle)) # Bounds
            return {"duty_cycle": duty_cycle, "runtime_hours": 24 * duty_cycle}

        on_cycles = [c["duration"] for c in cycles if c["type"] == "on"]
        off_cycles = [c["duration"] for c in cycles if c["type"] == "off"]
        
        total_on_time = sum(on_cycles)
        total_off_time = sum(off_cycles)
        total_cycle_time = total_on_time + total_off_time
        
        duty_cycle = total_on_time / total_cycle_time if total_cycle_time > 0 else 0.4
        duty_cycle = max(0.1, min(0.9, duty_cycle)) # Apply bounds

        # Scale duty cycle to represent a full 24h day based on observed period
        observed_hours = (timestamps[-1] - timestamps[0]) / 3600.0
        if observed_hours > 1 and observed_hours < 24: # Avoid scaling if already ~24h or too short
            scaling_factor = 24.0 / observed_hours
            runtime_hours = duty_cycle * 24.0 * scaling_factor
        else:
            runtime_hours = duty_cycle * 24.0
        
        runtime_hours = max(2.0, min(20.0, runtime_hours)) # Reasonable bounds for daily runtime

        return {"duty_cycle": duty_cycle, "runtime_hours": runtime_hours}

    def _group_data_by_day(self, temp_data, door_events):
        """Groups temperature and door events by calendar day (UTC)."""
        print("[ML] Grouping historical data by day...")
        grouped = defaultdict(lambda: {"temp_points": [], "door_events": []})
        
        for p in temp_data:
             try:
                  # Use UTC timezone for consistency
                  day_str = datetime.fromtimestamp(p['timestamp'], tz=timezone.utc).strftime("%Y-%m-%d")
                  grouped[day_str]["temp_points"].append(p)
             except (TypeError, ValueError, OSError): continue # Skip invalid timestamps

        for e in door_events:
             try:
                  day_str = datetime.fromtimestamp(e['timestamp'], tz=timezone.utc).strftime("%Y-%m-%d")
                  grouped[day_str]["door_events"].append(e)
             except (TypeError, ValueError, OSError): continue
                 
        # Sort points within each day (important for analysis)
        for day in grouped:
            grouped[day]["temp_points"].sort(key=lambda x: x['timestamp'])
            grouped[day]["door_events"].sort(key=lambda x: x['timestamp'])

        print(f"[ML] Grouped data into {len(grouped)} days.")
        return grouped

    ## ----------------------------------------
    ## REFACTORED ML TRAINING (using REAL data)
    ## ----------------------------------------

    def train_runtime_model(self, device_id, training_period="30d"):
        """Train ML model using REAL historical data to predict daily runtime."""
        
        # Prevent multiple threads training the same model simultaneously
        with self.training_lock:
            # Check if model exists and is recent enough (e.g., trained within last day)
            if device_id in self.ml_models:
                 last_trained_ts = self.ml_models[device_id].get("last_trained", 0)
                 if time.time() - last_trained_ts < 86400: # 24 hours
                      print(f"[ML] Personalized model for {device_id} is up-to-date. Skipping training.")
                      return self.ml_models[device_id]["model"]

            if not self.settings["ml"]["enable_predictions"]: return None
            print(f"[ML] Starting personalized training for {device_id} using data from last {training_period}...")

            # 1. Fetch Historical Data
            hist_temp = self.fetch_historical_temperature(device_id, duration=training_period)
            hist_events = self.fetch_historical_door_events(device_id, duration=training_period)

            # Need at least a few days worth of dense data
            min_required_points = 24 * 12 * 3 # ~3 days assuming 5min intervals
            if not hist_temp or len(hist_temp) < min_required_points: 
                print(f"[ML] Insufficient historical temperature data ({len(hist_temp)} points) for {device_id}. Min required: {min_required_points}")
                return None

            # 2. Feature Engineering & Target Calculation (Day by Day)
            features = []
            targets = []
            daily_data = self._group_data_by_day(hist_temp, hist_events) 

            if not daily_data:
                 print(f"[ML] Could not process historical data into daily features for {device_id}.")
                 return None

            valid_days_count = 0
            for day_str, data in daily_data.items():
                if not data["temp_points"] or len(data["temp_points"]) < 12 * 4: # Need at least 4 hours of data for a day
                    continue 

                day_dt = datetime.strptime(day_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                # Ensure we don't use today's possibly incomplete data for training
                if day_dt.date() >= datetime.now(timezone.utc).date():
                     continue

                temp_stats = self.analyze_temperature_data(data["temp_points"], period="1d") 
                usage_stats = self.analyze_door_usage(data["door_events"], period="1d")    

                feature_vector = {
                    "avg_temperature": temp_stats.get("avg_temperature", 4.0),
                    "temperature_variance": temp_stats.get("temperature_variance", 0.5),
                    "stability_score": temp_stats.get("stability_score", 80),
                    "daily_openings": usage_stats.get("avg_daily_openings", 0), 
                    "avg_door_duration": usage_stats.get("avg_duration_seconds", 0),
                    "day_of_week": day_dt.weekday(), 
                }

                # Calculate the ACTUAL runtime/duty cycle for that day (Target Variable)
                runtime_target = self.calculate_historical_runtime(data["temp_points"])

                if runtime_target["runtime_hours"] is not None:
                    features.append(feature_vector)
                    targets.append(runtime_target["runtime_hours"])
                    valid_days_count += 1

            if valid_days_count < self.settings["ml"]["min_training_samples"]:
                print(f"[ML] Not enough valid historical days ({valid_days_count}) for training {device_id}. Min required: {self.settings['ml']['min_training_samples']}")
                return None

            # 3. Prepare Data for Scikit-learn
            feature_names = sorted(list(features[0].keys())) # Sort for consistent order
            X = [[f[name] for name in feature_names] for f in features]
            y = np.array(targets)
            X = np.array(X)
            
            # 4. Train Model
            model = LinearRegression()
            model.fit(X, y) 

            # Evaluate
            y_pred = model.predict(X)
            mae = mean_absolute_error(y, y_pred)
            r2 = r2_score(y, y_pred)

            # Store the personalized model
            self.ml_models[device_id] = {
                "model": model,
                "last_trained": time.time(),
                "features": feature_names, # Store the features used for prediction
                "training_days": valid_days_count,
                "accuracy": {"mae": mae, "r2": r2}
            }
            
            print(f"[ML] Personalized runtime model trained for {device_id} ({valid_days_count} days) - MAE: {mae:.3f}h, R2: {r2:.3f}")
            return model

    ## ----------------------------------------
    ## REFACTORED ML PREDICTION (using personalized model)
    ## ----------------------------------------

    def predict_runtime(self, device_id, future_days=7):
        """Predict daily runtime using the personalized model."""

        # Trigger training if model doesn't exist or is old
        model_obj = None
        if device_id not in self.ml_models:
             print(f"[ML] No personalized model found for {device_id}. Triggering training...")
             model_obj = self.train_runtime_model(device_id)
        else:
             last_trained_ts = self.ml_models[device_id].get("last_trained", 0)
             if time.time() - last_trained_ts > 86400 * 7: # Re-train weekly
                  print(f"[ML] Personalized model for {device_id} is older than 1 week. Triggering re-training...")
                  model_obj = self.train_runtime_model(device_id)
             else:
                  model_obj = self.ml_models[device_id]["model"] # Use existing model

        if not model_obj:
            print(f"[ML] No valid model available for {device_id}. Cannot predict.")
            return None # Return None if training failed or model not available
            
        model_data = self.ml_models[device_id]
        model = model_data["model"]
        feature_names = model_data["features"] # Use features the model was trained on
        
        print(f"[ML] Generating {future_days}-day forecast for {device_id} using personalized model trained on {model_data.get('training_days','N/A')} days.")

        # Get CURRENT average stats from the last few days to base prediction on
        # Using a shorter period (e.g., 3d) might make predictions more reactive to recent changes
        recent_period = "3d"
        temp_data = self.fetch_temperature_series_from_adaptor(device_id, recent_period)
        door_events = self.fetch_door_events_from_adaptor(device_id, recent_period)
        if not temp_data: 
             print(f"[ML] Insufficient recent data ({recent_period}) to base predictions on for {device_id}.")
             return None 
        
        current_temp_analysis = self.analyze_temperature_data(temp_data, recent_period)
        current_usage_analysis = self.analyze_door_usage(door_events, recent_period)

        predictions = []
        power_specs = self.get_device_power_specs(device_id)
        compressor_power = power_specs.get("base_power_watts", 120)

        for day_offset in range(future_days):
            future_timestamp = time.time() + (day_offset * 24 * 3600)
            dt = datetime.fromtimestamp(future_timestamp, tz=timezone.utc)
            
            # --- Generate features for the future day ---
            # Use recent averages, but apply the correct future day_of_week
            features_for_prediction = {
                "avg_temperature": current_temp_analysis.get("avg_temperature", 4.0),
                "temperature_variance": current_temp_analysis.get("temperature_variance", 0.5),
                "stability_score": current_temp_analysis.get("stability_score", 80),
                "daily_openings": current_usage_analysis.get("avg_daily_openings", 8),
                "avg_door_duration": current_usage_analysis.get("avg_duration_seconds", 30),
                "day_of_week": dt.weekday(), 
            }
            # Add default 0 for any other features the model expects
            for name in feature_names:
                if name not in features_for_prediction: features_for_prediction[name] = 0.0

            # Create feature vector in the correct order
            try:
                feature_vector = [features_for_prediction[fname] for fname in feature_names]
                feature_vector = np.array(feature_vector).reshape(1, -1)
            except KeyError as e:
                print(f"[ML] Prediction failed: Feature mismatch. Model expects '{e}'. Available: {list(features_for_prediction.keys())}")
                return None 

            # Make prediction
            predicted_runtime = model.predict(feature_vector)[0]
            predicted_runtime = max(2.0, min(20.0, predicted_runtime)) # Bounds
            predicted_kwh = (predicted_runtime * compressor_power) / 1000
            
            predictions.append({
                "day": day_offset + 1,
                "date": dt.strftime("%Y-%m-%d"),
                "predicted_runtime_hours": round(predicted_runtime, 2),
                "predicted_kwh": round(predicted_kwh, 3),
                "timestamp": int(future_timestamp) 
            })
            
        print(f"[ML] Forecast generated for {device_id}.")
        return predictions

    ## ----------------------------------------
    ## REMOVED FUNCTION
    ## ----------------------------------------
    # def generate_runtime_training_data(self, device_id, samples=100): <--- REMOVED

    ## ----------------------------------------
    ## CORE ANALYSIS FUNCTIONS (Mostly unchanged, but use correct power param)
    ## ----------------------------------------

    # Copied from Data_Analysis.py - used internally now
    def analyze_temperature_data(self, temp_data, period):
        if not temp_data: return {"stability_score": 0, "avg_temperature": 4.0, "temperature_variance": 1.0}
        temperatures = [point["value"] for point in temp_data]
        if not temperatures: return {"stability_score": 0, "avg_temperature": 4.0, "temperature_variance": 1.0}
        avg_temp = np.mean(temperatures); temp_variance = np.var(temperatures); temp_std = np.std(temperatures)
        if temp_std < 0.5: stability_score = 95
        elif temp_std < 1.0: stability_score = 85
        elif temp_std < 1.5: stability_score = 75
        else: stability_score = max(0, 70 - (temp_std - 1.5) * 10)
        return {"avg_temperature": round(avg_temp, 2), "temperature_variance": round(temp_variance, 3), "stability_score": round(stability_score, 1)}

    # Copied from Data_Analysis.py - used internally now
    def analyze_door_usage(self, door_events, period):
        if not door_events: return {"avg_daily_openings": 0, "avg_duration_seconds": 0}
        closed_events = [e for e in door_events if e.get("event_type") == "door_closed" and e.get("duration") is not None]
        if not closed_events: return {"avg_daily_openings": 0, "avg_duration_seconds": 0}
        durations = [e["duration"] for e in closed_events]; avg_duration = np.mean(durations) if durations else 0
        period_days = self.period_to_days(period); avg_daily_openings = len(closed_events) / period_days if period_days > 0 else len(closed_events)
        return {"total_openings": len(closed_events), "avg_daily_openings": round(avg_daily_openings, 1), "avg_duration_seconds": round(avg_duration, 1)}

    # Copied from Data_Analysis.py - Helper
    def period_to_days(self, period):
        if isinstance(period, (int, float)): return period # Assume already days if numeric
        if isinstance(period, str):
            if period.endswith("d"): return int(period[:-1])
            elif period.endswith("h"): return int(period[:-1]) / 24
            elif period.endswith("w"): return int(period[:-1]) * 7
        return 7 # Default fallback

    # analyze_compressor_cycles remains the same (analyzes REAL temp data)
    def analyze_compressor_cycles(self, temperature_series, power_specs, period_info=None):
        """
        Estimate compressor duty cycle based on temperature thresholds AND
        count cycles/measure durations based on detected state changes.
        """
        if len(temperature_series) < 20: # Need at least ~2 hours of data for meaningful cycle counting
            print(f"[CYCLE] Insufficient data ({len(temperature_series)} points) for threshold/cycle analysis. Using fallback.")
            return {
                "estimated_duty_cycle": 0.4, "cycle_count": 0,
                "avg_on_duration_minutes": 0, "avg_off_duration_minutes": 0,
                "confidence": 0.1, "analysis_period": period_info or "insufficient_data"
            }

        temperatures = [point["value"] for point in temperature_series]
        timestamps = [point["timestamp"] for point in temperature_series]
        
        # Get thresholds
        temp_min = 3.5
        temp_max = 4.5

        total_duration_seconds = timestamps[-1] - timestamps[0]
        if total_duration_seconds <= 0:
             return { # Handle invalid total duration
                "estimated_duty_cycle": 0.0, "cycle_count": 0,
                "avg_on_duration_minutes": 0, "avg_off_duration_minutes": 0,
                "confidence": 0.1, "analysis_period": period_info or "invalid_duration"
             }

        estimated_on_time_seconds = 0
        
        # --- Cycle Counting/Duration Variables ---
        on_durations = []
        off_durations = []
        cycle_count = 0
        current_state = None # Can be 'ON' or 'OFF'
        current_phase_start_time = timestamps[0]
        # ---

        # Estimate initial state based on first point
        if temperatures[0] >= temp_max:
            current_state = 'ON'
        elif temperatures[0] <= temp_min:
             current_state = 'OFF'
        # If between thresholds, guess based on next point (if available)
        elif len(temperatures) > 1 and temperatures[1] < temperatures[0]:
             current_state = 'ON' # Likely cooling
        else:
             current_state = 'OFF' # Likely warming or stable


        # Iterate through intervals
        for i in range(1, len(temperatures)):
            t_start = timestamps[i-1]
            t_end = timestamps[i]
            temp_start = temperatures[i-1]
            temp_end = temperatures[i]
            interval_duration_seconds = t_end - t_start

            if interval_duration_seconds <= 0 or interval_duration_seconds > 3600 * 2: # Skip invalid intervals or gaps > 2hr
                # If gap is large, reset phase tracking
                current_phase_start_time = t_end
                # Re-estimate state after the gap
                if temp_end >= temp_max: current_state = 'ON'
                elif temp_end <= temp_min: current_state = 'OFF'
                # Else keep previous state assumption for now
                continue

            # --- Estimate state for THIS interval ---
            compressor_likely_on_this_interval = False
            if temp_start >= temp_max:
                compressor_likely_on_this_interval = True
            elif temp_start > temp_min and temp_end < temp_start and (temp_start - temp_end) > 0.05:
                compressor_likely_on_this_interval = True
            
            estimated_state_this_interval = 'ON' if compressor_likely_on_this_interval else 'OFF'

            # --- Track Total ON Time ---
            if estimated_state_this_interval == 'ON':
                estimated_on_time_seconds += interval_duration_seconds

            # --- Detect State Change for Cycle Counting ---
            if estimated_state_this_interval != current_state:
                phase_duration_seconds = t_end - current_phase_start_time
                phase_duration_minutes = phase_duration_seconds / 60.0

                # Store duration of the phase that just ended
                # Apply a minimum duration threshold (e.g., 2 minutes) to count as a valid phase
                min_phase_duration_minutes = 2.0
                if phase_duration_minutes >= min_phase_duration_minutes:
                    if current_state == 'ON':
                        on_durations.append(phase_duration_minutes)
                    else: # current_state == 'OFF'
                        off_durations.append(phase_duration_minutes)
                        # Count a cycle when an OFF phase ends and ON begins
                        if estimated_state_this_interval == 'ON':
                             cycle_count += 1
                
                # Update state for the new phase
                current_state = estimated_state_this_interval
                current_phase_start_time = t_end

        # --- Handle the final phase after the loop ---
        final_phase_duration_seconds = timestamps[-1] - current_phase_start_time
        final_phase_duration_minutes = final_phase_duration_seconds / 60.0
        min_phase_duration_minutes = 2.0 # Use same threshold
        if final_phase_duration_minutes >= min_phase_duration_minutes:
            if current_state == 'ON':
                on_durations.append(final_phase_duration_minutes)
            else:
                 off_durations.append(final_phase_duration_minutes)
                 # Count cycle if the very first state was ON and the last was OFF
                 if cycle_count == 0 and len(on_durations) > 0 and len(off_durations) > 0:
                      # This catches cases with only one ON and one OFF phase if loop didn't catch it
                      if on_durations[0] >= min_phase_duration_minutes and off_durations[0] >= min_phase_duration_minutes:
                           cycle_count = 1 # At least one cycle occurred

        # --- Calculate Final Metrics ---
        estimated_duty_cycle = estimated_on_time_seconds / total_duration_seconds
        estimated_duty_cycle = max(0.0, min(1.0, estimated_duty_cycle))
        if estimated_on_time_seconds > 0 and estimated_duty_cycle < 0.05:
            estimated_duty_cycle = 0.05 # Min floor if ON time > 0

        avg_on_duration = np.mean(on_durations) if on_durations else 0
        avg_off_duration = np.mean(off_durations) if off_durations else 0

        # Confidence (can now incorporate cycle count)
        points_factor = min(1.0, len(temperature_series) / 200.0)
        duration_hours = total_duration_seconds / 3600.0
        duration_factor = min(1.0, duration_hours / 24.0)
        # Give bonus confidence if a reasonable number of cycles were detected
        cycle_factor = min(1.0, cycle_count / 5.0) # Max bonus after 5 cycles
        confidence = (points_factor * 0.4 + duration_factor * 0.4 + cycle_factor * 0.2) * 0.9 + 0.1 # Weighted average + base
        confidence = min(1.0, confidence) # Ensure it doesn't exceed 100%

        analysis_hours = duration_hours

        print(f"[CYCLE_THRESHOLD+] Duty cycle: {estimated_duty_cycle:.3f}, Cycles: {cycle_count}, "
              f"Avg ON: {avg_on_duration:.1f}m, Avg OFF: {avg_off_duration:.1f}m "
              f"(Conf: {confidence:.2f}, Based on {analysis_hours:.1f}h)")

        return {
            "estimated_duty_cycle": round(estimated_duty_cycle, 3),
            "cycle_count": cycle_count, # Now calculated
            "avg_on_duration_minutes": round(avg_on_duration, 1) if avg_on_duration > 0 else 0, # Calculated
            "avg_off_duration_minutes": round(avg_off_duration, 1) if avg_off_duration > 0 else 0, # Calculated
            "confidence": round(confidence, 2),
            "analysis_period": period_info or f"{analysis_hours:.1f}h"
        }

    # calculate_door_runtime_penalty remains the same
    def calculate_door_runtime_penalty(self, door_events, power_specs):
        if not door_events: return 0
        total_extra_runtime = 0; recovery_mult = power_specs.get("recovery_time_multiplier", 1.5)
        for event in door_events:
            if event.get("event_type") == "door_closed" and event.get("duration"):
                extra_runtime = (event["duration"] / 60.0) * recovery_mult
                total_extra_runtime += extra_runtime
        return total_extra_runtime / 60.0 # Hours

    # estimate_daily_energy_consumption uses correct power param name now
    def estimate_daily_energy_consumption(self, device_id, temp_analysis, usage_analysis, cycle_analysis, power_specs):
        base_duty_cycle = cycle_analysis["estimated_duty_cycle"]
        daily_openings = usage_analysis.get("avg_daily_openings", 0)
        avg_duration_min = usage_analysis.get("avg_duration_seconds", 0) / 60.0
        recovery_mult = power_specs.get("recovery_time_multiplier", 1.5)
        daily_door_penalty_hours = (daily_openings * avg_duration_min * recovery_mult) # Calculation error fixed: was dividing by 60 twice
        
        stability_score = temp_analysis.get("stability_score", 80)
        temp_factor = 1.2 if stability_score < 70 else (0.9 if stability_score > 90 else 1.0)
        
        base_daily_runtime_hours = 24 * base_duty_cycle
        # Ensure penalty is not excessively large compared to base runtime
        daily_door_penalty_hours = min(daily_door_penalty_hours, base_daily_runtime_hours * 0.5) # Cap penalty at 50% of base runtime
        
        total_daily_runtime_hours = (base_daily_runtime_hours + daily_door_penalty_hours) * temp_factor
        total_daily_runtime_hours = max(2.0, min(24.0, total_daily_runtime_hours)) # Bounds [2h, 24h]

        compressor_power_watts = power_specs.get("base_power_watts", 120) # Correct param name
        daily_kwh = (total_daily_runtime_hours * compressor_power_watts) / 1000
        
        return {
            "daily_kwh": round(daily_kwh, 3), "runtime_hours_per_day": round(total_daily_runtime_hours, 2),
            "base_duty_cycle": round(base_duty_cycle, 3), "base_runtime_hours": round(base_daily_runtime_hours, 2),
            "door_penalty_hours": round(daily_door_penalty_hours, 2), "temperature_factor": round(temp_factor, 2),
            "compressor_power_watts": compressor_power_watts, "cycle_analysis": cycle_analysis
        }

    # generate_recommendations uses correct power param name now
    def generate_recommendations(self, device_id, temp_analysis, usage_analysis, energy_estimate, power_specs):
        recommendations = []
        compressor_power = power_specs.get("base_power_watts", 120) # Correct param
        
        daily_openings = usage_analysis.get("avg_daily_openings", 0)
        if daily_openings > power_specs.get("max_efficient_openings_per_day", 15):
            # Estimate savings more realistically based on reducing openings *towards* the target
            openings_to_reduce = daily_openings - power_specs.get("max_efficient_openings_per_day", 15)
            avg_duration_sec = usage_analysis.get("avg_duration_seconds", 30)
            recovery_mult = power_specs.get("recovery_time_multiplier", 1.5)
            # Savings in hours per day = (N_reduced * duration_min * recovery) / 60
            runtime_savings = (openings_to_reduce * (avg_duration_sec / 60.0) * recovery_mult) 
            kwh_savings = (runtime_savings * compressor_power) / 1000
            
            recommendations.append({
                "type": "behavioral", "priority": "medium", # Lowered priority slightly
                "message": f"Reduce door openings: {daily_openings:.1f}/day. Target: <{power_specs.get('max_efficient_openings_per_day', 15)}/day",
                "potential_savings_kwh_day": round(kwh_savings, 3),
            })
        
        stability_score = temp_analysis.get("stability_score", 80)
        if stability_score < 70:
            # Estimate savings assuming stability improves runtime by ~10-15%
            runtime_savings = energy_estimate["runtime_hours_per_day"] * 0.10 
            kwh_savings = (runtime_savings * compressor_power) / 1000
            recommendations.append({
                "type": "maintenance", "priority": "medium",
                "message": f"Unstable temperature (stability: {stability_score:.1f}%). Check door seals.",
                "potential_savings_kwh_day": round(kwh_savings, 3),
            })
        
        if energy_estimate["base_duty_cycle"] > 0.6: # Use a slightly higher threshold for the alert
             # Provide more context if confidence is low
             confidence = energy_estimate["cycle_analysis"].get("confidence", 0)
             confidence_msg = f"(Confidence: {confidence*100:.0f}%)" if confidence < 0.5 else ""
             recommendations.append({
                 "type": "alert", "priority": "high",
                 "message": f"High duty cycle ({energy_estimate['base_duty_cycle']*100:.1f}%). Possible malfunction or overload. {confidence_msg}".strip(),
             })
        
        return recommendations

    ## ----------------------------------------
    ## UPDATED MAIN ANALYSIS FUNCTION
    ## ----------------------------------------

    def analyze_device_energy(self, device_id, period="7d"):
        """Main analysis function using REAL data & PERSONALIZED predictions."""
        print(f"[ANALYSIS] Analyzing {device_id} with REAL data model (Period: {period})")

        power_specs = self.get_device_power_specs(device_id)

        # *** CORRECTION: Use the correct method name here ***
        temp_data = self.fetch_historical_temperature(device_id, period)
        # *** CORRECTION: Use the correct method name here ***
        door_events = self.fetch_historical_door_events(device_id, period)

        # Check if we got enough *recent* data for reliable current analysis
        # Using 50 points as a threshold for a 7d period is reasonable
        if not temp_data or len(temp_data) < 50:
            print(f"[ANALYSIS] Insufficient recent temperature data ({len(temp_data)} points) for {device_id} over '{period}'. Cannot perform full analysis.")
            return {
                "device_id": device_id, "error": "Insufficient data",
                "message": f"Need more recent temperature data (have {len(temp_data)}, need ~50+) for analysis over period '{period}'.",
                "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
                "service": self.service_id
            }

        temp_analysis = self.analyze_temperature_data(temp_data, period)
        usage_analysis = self.analyze_door_usage(door_events, period)
        cycle_analysis = self.analyze_compressor_cycles(temp_data, power_specs, period_info=period)

        energy_estimate = self.estimate_daily_energy_consumption(
            device_id, temp_analysis, usage_analysis, cycle_analysis, power_specs
        )

        predictions = None
        if self.settings["ml"]["enable_predictions"]:
            predictions = self.predict_runtime(device_id, future_days=7)

        recommendations = self.generate_recommendations(
            device_id, temp_analysis, usage_analysis, energy_estimate, power_specs
        )

        result = {
            "device_id": device_id,
            "model": self.device_models.get(device_id, "unknown"),
            "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
            "analysis_method": "real_duty_cycle_personalized_prediction",
            "current_energy": energy_estimate, # Includes nested cycle_analysis
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

    ## ----------------------------------------
    ## REST API & SERVICE RUN/SHUTDOWN (Unchanged)
    ## ----------------------------------------
    def setup_rest_api(self):
        try:
            cherrypy.config.update({'server.socket_host': '0.0.0.0', 'server.socket_port': 8003, 'engine.autoreload.on': False, 'log.screen': False})
            cherrypy.tree.mount(EnergyOptimizationRestAPI(self), '/', {'/': {'tools.response_headers.on': True, 'tools.response_headers.headers': [('Content-Type', 'application/json')]}})
            def start_server(): cherrypy.engine.start(); print("[REST] Server started on port 8003")
            self.rest_server_thread = threading.Thread(target=start_server, daemon=True); self.rest_server_thread.start(); time.sleep(2)
            return True
        except Exception as e: print(f"[REST] Failed to start server: {e}"); return False

    def get_status(self):
        model_info = {dev: {"trained_days": data.get("training_days", "N/A"), "last_trained": time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(data.get("last_trained", 0)))} for dev, data in self.ml_models.items()}
        return {
            "service_id": self.service_id, "status": "running" if self.running else "stopped",
            "analysis_method": "real_duty_cycle_personalized_prediction", "known_devices": len(self.known_devices),
            "trained_personalized_models": len(self.ml_models), "ml_enabled": self.settings["ml"]["enable_predictions"],
            "adaptor_url": self.influx_adaptor_url, "model_details": model_info
        }

    def run(self):
        print("="*60 + "\n    SMARTCHILL ENERGY OPTIMIZATION SERVICE (PERSONALIZED ML)\n" + "="*60)
        if not self.setup_rest_api(): print("[ERROR] Failed to setup REST API"); return
        if not self.register_with_catalog(): print("[WARN] Failed to register with catalog")
        self.load_devices_and_models_from_catalog()
        print(f"[INIT] Service started successfully! REST API: http://localhost:8003")
        print(f"[INIT] Analyzing REAL data via {self.influx_adaptor_url}")
        print(f"[INIT] Using PERSONALIZED ML models for prediction.")
        try:
            # Periodically re-train models in background? (Optional enhancement)
            while self.running: time.sleep(1)
        except KeyboardInterrupt: print("\n[SHUTDOWN] Received interrupt signal..."); self.shutdown()

    def shutdown(self):
        print("[SHUTDOWN] Stopping service..."); self.running = False
        if self.rest_server_thread:
            try: cherrypy.engine.exit(); print("[SHUTDOWN] REST API stopped")
            except Exception as e: print(f"[SHUTDOWN] Error stopping REST API: {e}")
        print("[SHUTDOWN] Service stopped")

## ----------------------------------------
## REST API CLASS (Unchanged logic, uses new analyze_device_energy)
## ----------------------------------------
class EnergyOptimizationRestAPI:
    def __init__(self, service): self.service = service

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def health(self): return {"status": "healthy", "service": "Energy Optimization", "method": "real_duty_cycle_personalized_prediction", "timestamp": datetime.now(timezone.utc).isoformat()}

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def status(self): return self.service.get_status()

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def optimize(self, device_id, period="7d"): # Added period parameter
        try:
            if device_id not in self.service.known_devices: cherrypy.response.status = 404; return {"error": f"Device {device_id} not found"}
            result = self.service.analyze_device_energy(device_id, period) # Pass period
            if not result or "error" in result: # Check if analysis returned an error (e.g., insufficient data)
                 cherrypy.response.status = 400 if result and "Insufficient data" in result.get("error","") else 500
                 return result or {"error": f"Failed to analyze device {device_id}", "reason": "Unknown analysis error"}
            return result
        except Exception as e: print(f"[REST] Error in optimize endpoint: {e}"); cherrypy.response.status = 500; return {"error": "Internal server error", "details": str(e)}

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def predictions(self, device_id):
        try:
            if device_id not in self.service.known_devices: cherrypy.response.status = 404; return {"error": f"Device {device_id} not found"}
            if not self.service.settings["ml"]["enable_predictions"]: cherrypy.response.status = 503; return {"error": "ML predictions disabled"}
            
            predictions = self.service.predict_runtime(device_id, future_days=7) # This will trigger training if needed
            
            if predictions is None: # Handle case where training failed or no model available
                cherrypy.response.status = 500
                # Check if model exists but failed prediction, or if model never trained
                reason = "Failed to generate predictions"
                if device_id not in self.service.ml_models: reason += " (Model not trained - insufficient historical data?)"
                elif not self.service.ml_models[device_id].get("model"): reason += " (Model training failed)"
                else: reason += " (Could not fetch recent data for prediction input)"
                return {"error": f"Failed to generate predictions for {device_id}", "reason": reason}
            
            model_info = {}
            if device_id in self.service.ml_models:
                m_data = self.service.ml_models[device_id]
                model_info = {"last_trained": datetime.fromtimestamp(m_data["last_trained"], tz=timezone.utc).isoformat(), "accuracy": m_data.get("accuracy", {}), "training_days": m_data.get("training_days")}
            
            return {
                "device_id": device_id, "model": self.service.device_models.get(device_id, "unknown"),
                "predictions": predictions, "model_info": model_info, "analysis_method": "personalized_prediction",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        except Exception as e: print(f"[REST] Error in predictions endpoint: {e}"); cherrypy.response.status = 500; return {"error": "Internal server error", "details": str(e)}

    @cherrypy.expose  
    @cherrypy.tools.json_out()
    def runtime(self, device_id, period="7d"): # Added period parameter
        try:
            if device_id not in self.service.known_devices: cherrypy.response.status = 404; return {"error": f"Device {device_id} not found"}
            
            analysis_result = self.service.analyze_device_energy(device_id, period=period) # Get full analysis
            
            if not analysis_result or "error" in analysis_result:
                cherrypy.response.status = 400 if analysis_result and "Insufficient data" in analysis_result.get("error","") else 500
                return analysis_result or {"error": "Failed to analyze runtime"}
                
            power_specs = self.service.get_device_power_specs(device_id)
            
            # Return a subset focused on runtime breakdown
            return {
                "device_id": device_id,
                "analysis_method": analysis_result.get("analysis_method"),
                "timestamp": analysis_result.get("analysis_timestamp"),
                "cycle_analysis": analysis_result.get("current_energy", {}).get("cycle_analysis", {}),
                "energy_breakdown": analysis_result.get("current_energy", {}),
                "statistics": analysis_result.get("statistics", {}),
                "power_specs_used": power_specs,
                "period_analyzed": period
            }
        except Exception as e: print(f"[REST] Error in runtime endpoint: {e}"); cherrypy.response.status = 500; return {"error": "Internal server error"}

def main():
    service = EnergyOptimizationService()
    try: service.run()
    except Exception as e: print(f"[FATAL] Service error: {e}"); import traceback; traceback.print_exc()
    finally: service.shutdown()

if __name__ == "__main__":
    main()