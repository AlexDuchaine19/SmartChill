import time
import threading
import numpy as np
from datetime import datetime, timezone
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, r2_score
from collections import defaultdict

class MLEngine:
    def __init__(self, settings, analyzer):
        self.settings = settings
        self.analyzer = analyzer
        self.ml_models = {} # {device_id: {"model": model, "features": [...], ...}}
        self.training_lock = threading.Lock()

    def _group_data_by_day(self, temp_data, door_events):
        """Groups temperature and door events by calendar day (UTC)."""
        print("[ML] Grouping historical data by day...")
        grouped = defaultdict(lambda: {"temp_points": [], "door_events": []})
        
        for p in temp_data:
             try:
                  day_str = datetime.fromtimestamp(p['timestamp'], tz=timezone.utc).strftime("%Y-%m-%d")
                  grouped[day_str]["temp_points"].append(p)
             except (TypeError, ValueError, OSError): continue

        for e in door_events:
             try:
                  day_str = datetime.fromtimestamp(e['timestamp'], tz=timezone.utc).strftime("%Y-%m-%d")
                  grouped[day_str]["door_events"].append(e)
             except (TypeError, ValueError, OSError): continue
                 
        for day in grouped:
            grouped[day]["temp_points"].sort(key=lambda x: x['timestamp'])
            grouped[day]["door_events"].sort(key=lambda x: x['timestamp'])

        print(f"[ML] Grouped data into {len(grouped)} days.")
        return grouped

    def calculate_historical_runtime(self, daily_temp_data):
        """Calculate the actual duty cycle/runtime for a given day's temperature data."""
        if len(daily_temp_data) < 20:
            return {"duty_cycle": None, "runtime_hours": None}

        temperatures = [p['value'] for p in daily_temp_data]
        timestamps = [p['timestamp'] for p in daily_temp_data]
        
        cycles = []
        current_trend = "unknown"
        cycle_start_index = 0
        temp_threshold = 0.1

        for i in range(1, len(temperatures)):
            temp_change = temperatures[i] - temperatures[i-1]
            time_diff_minutes = (timestamps[i] - timestamps[i-1]) / 60.0

            if time_diff_minutes <= 0 or time_diff_minutes > 60: continue

            if temp_change < -temp_threshold / time_diff_minutes and current_trend != "cooling":
                if current_trend == "warming":
                    cycle_duration = (timestamps[i] - timestamps[cycle_start_index]) / 60
                    if cycle_duration < 120: cycles.append({"type": "off", "duration": cycle_duration})
                current_trend = "cooling"
                cycle_start_index = i
                
            elif temp_change > temp_threshold / time_diff_minutes and current_trend != "warming":
                if current_trend == "cooling":
                    cycle_duration = (timestamps[i] - timestamps[cycle_start_index]) / 60
                    if cycle_duration < 120: cycles.append({"type": "on", "duration": cycle_duration})
                current_trend = "warming"
                cycle_start_index = i
        
        if not cycles:
            avg_temp = np.mean(temperatures)
            time_below_avg = sum((timestamps[i] - timestamps[i-1]) / 60.0 
                                 for i in range(1, len(timestamps)) if temperatures[i] < avg_temp)
            total_time = (timestamps[-1] - timestamps[0]) / 60.0
            duty_cycle = time_below_avg / total_time if total_time > 0 else 0.4
            duty_cycle = max(0.1, min(0.9, duty_cycle))
            return {"duty_cycle": duty_cycle, "runtime_hours": 24 * duty_cycle}

        on_cycles = [c["duration"] for c in cycles if c["type"] == "on"]
        off_cycles = [c["duration"] for c in cycles if c["type"] == "off"]
        
        total_on_time = sum(on_cycles)
        total_off_time = sum(off_cycles)
        total_cycle_time = total_on_time + total_off_time
        
        duty_cycle = total_on_time / total_cycle_time if total_cycle_time > 0 else 0.4
        duty_cycle = max(0.1, min(0.9, duty_cycle))

        observed_hours = (timestamps[-1] - timestamps[0]) / 3600.0
        if observed_hours > 1 and observed_hours < 24:
            scaling_factor = 24.0 / observed_hours
            runtime_hours = duty_cycle * 24.0 * scaling_factor
        else:
            runtime_hours = duty_cycle * 24.0
        
        runtime_hours = max(2.0, min(20.0, runtime_hours))

        return {"duty_cycle": duty_cycle, "runtime_hours": runtime_hours}

    def train_runtime_model(self, device_id, hist_temp, hist_events):
        """Train ML model using REAL historical data to predict daily runtime."""
        with self.training_lock:
            if device_id in self.ml_models:
                 last_trained_ts = self.ml_models[device_id].get("last_trained", 0)
                 if time.time() - last_trained_ts < 86400:
                      print(f"[ML] Personalized model for {device_id} is up-to-date. Skipping training.")
                      return self.ml_models[device_id]["model"]

            if not self.settings["ml"]["enable_predictions"]: return None
            print(f"[ML] Starting personalized training for {device_id}...")

            min_required_points = 7
            if not hist_temp or len(hist_temp) < min_required_points: 
                print(f"[ML] Insufficient historical temperature data ({len(hist_temp)} points) for {device_id}.")
                return None

            features = []
            targets = []
            daily_data = self._group_data_by_day(hist_temp, hist_events) 

            if not daily_data:
                 print(f"[ML] Could not process historical data into daily features for {device_id}.")
                 return None

            valid_days_count = 0
            for day_str, data in daily_data.items():
                if not data["temp_points"] or len(data["temp_points"]) < 12 * 4:
                    continue 

                day_dt = datetime.strptime(day_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if day_dt.date() >= datetime.now(timezone.utc).date():
                     continue

                temp_stats = self.analyzer.analyze_temperature_data(data["temp_points"], period="1d") 
                usage_stats = self.analyzer.analyze_door_usage(data["door_events"], period="1d")    

                feature_vector = {
                    "avg_temperature": temp_stats.get("avg_temperature", 4.0),
                    "temperature_variance": temp_stats.get("temperature_variance", 0.5),
                    "stability_score": temp_stats.get("stability_score", 80),
                    "daily_openings": usage_stats.get("avg_daily_openings", 0), 
                    "avg_door_duration": usage_stats.get("avg_duration_seconds", 0),
                    "day_of_week": day_dt.weekday(), 
                }

                runtime_target = self.calculate_historical_runtime(data["temp_points"])

                if runtime_target["runtime_hours"] is not None:
                    features.append(feature_vector)
                    targets.append(runtime_target["runtime_hours"])
                    valid_days_count += 1

            if valid_days_count < self.settings["ml"]["min_training_samples"]:
                print(f"[ML] Not enough valid historical days ({valid_days_count}) for training {device_id}.")
                return None

            feature_names = sorted(list(features[0].keys()))
            X = [[f[name] for name in feature_names] for f in features]
            y = np.array(targets)
            X = np.array(X)
            
            model = LinearRegression()
            model.fit(X, y) 

            y_pred = model.predict(X)
            mae = mean_absolute_error(y, y_pred)
            r2 = r2_score(y, y_pred)

            self.ml_models[device_id] = {
                "model": model,
                "last_trained": time.time(),
                "features": feature_names,
                "training_days": valid_days_count,
                "accuracy": {"mae": mae, "r2": r2}
            }
            
            print(f"[ML] Personalized runtime model trained for {device_id} ({valid_days_count} days) - MAE: {mae:.3f}h, R2: {r2:.3f}")
            return model

    def predict_runtime(self, device_id, current_temp_analysis, current_usage_analysis, compressor_power, future_days=7):
        """Predict daily runtime using the personalized model."""
        if device_id not in self.ml_models:
            print(f"[ML] No valid model available for {device_id}. Cannot predict.")
            return None
            
        model_data = self.ml_models[device_id]
        model = model_data["model"]
        feature_names = model_data["features"]
        
        print(f"[ML] Generating {future_days}-day forecast for {device_id}...")

        predictions = []

        for day_offset in range(future_days):
            future_timestamp = time.time() + (day_offset * 24 * 3600)
            dt = datetime.fromtimestamp(future_timestamp, tz=timezone.utc)
            
            features_for_prediction = {
                "avg_temperature": current_temp_analysis.get("avg_temperature", 4.0),
                "temperature_variance": current_temp_analysis.get("temperature_variance", 0.5),
                "stability_score": current_temp_analysis.get("stability_score", 80),
                "daily_openings": current_usage_analysis.get("avg_daily_openings", 8),
                "avg_door_duration": current_usage_analysis.get("avg_duration_seconds", 30),
                "day_of_week": dt.weekday(), 
            }
            for name in feature_names:
                if name not in features_for_prediction: features_for_prediction[name] = 0.0

            try:
                feature_vector = [features_for_prediction[fname] for fname in feature_names]
                feature_vector = np.array(feature_vector).reshape(1, -1)
            except KeyError as e:
                print(f"[ML] Prediction failed: Feature mismatch. Model expects '{e}'.")
                return None 

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
            
        print(f"[ML] Forecast generated for {device_id}.")
        return predictions
