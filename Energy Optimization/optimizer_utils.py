import numpy as np
import time
from datetime import datetime, timezone
from collections import defaultdict
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, r2_score

# ===================== Helper Functions =====================

def period_to_days(period):
    """Convert period string to number of days"""
    if isinstance(period, (int, float)): return period
    if isinstance(period, str):
        if period.endswith("d"): return int(period[:-1])
        elif period.endswith("h"): return int(period[:-1]) / 24
        elif period.endswith("w"): return int(period[:-1]) * 7
    return 7

def group_data_by_day(temp_data, door_events):
    """Groups temperature and door events by calendar day (UTC)"""
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
                
    # Sort points within each day
    for day in grouped:
        grouped[day]["temp_points"].sort(key=lambda x: x['timestamp'])
        grouped[day]["door_events"].sort(key=lambda x: x['timestamp'])

    return grouped

# ===================== Analysis Logic =====================

def analyze_temperature_data(temp_data, period):
    if not temp_data: return {"stability_score": 0, "avg_temperature": 4.0, "temperature_variance": 1.0}
    temperatures = [point["value"] for point in temp_data]
    if not temperatures: return {"stability_score": 0, "avg_temperature": 4.0, "temperature_variance": 1.0}
    
    avg_temp = np.mean(temperatures)
    temp_variance = np.var(temperatures)
    temp_std = np.std(temperatures)
    
    if temp_std < 0.5: stability_score = 95
    elif temp_std < 1.0: stability_score = 85
    elif temp_std < 1.5: stability_score = 75
    else: stability_score = max(0, 70 - (temp_std - 1.5) * 10)
    
    return {
        "avg_temperature": round(avg_temp, 2), 
        "temperature_variance": round(temp_variance, 3), 
        "stability_score": round(stability_score, 1)
    }

def analyze_door_usage(door_events, period):
    if not door_events: return {"avg_daily_openings": 0, "avg_duration_seconds": 0}
    
    closed_events = [e for e in door_events if e.get("event_type") == "door_closed" and e.get("duration") is not None]
    if not closed_events: return {"avg_daily_openings": 0, "avg_duration_seconds": 0}
    
    durations = [e["duration"] for e in closed_events]
    avg_duration = np.mean(durations) if durations else 0
    period_days = period_to_days(period)
    avg_daily_openings = len(closed_events) / period_days if period_days > 0 else len(closed_events)
    
    return {
        "total_openings": len(closed_events), 
        "avg_daily_openings": round(avg_daily_openings, 1), 
        "avg_duration_seconds": round(avg_duration, 1)
    }

def calculate_historical_runtime(daily_temp_data):
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
    else:
        on_cycles = [c["duration"] for c in cycles if c["type"] == "on"]
        off_cycles = [c["duration"] for c in cycles if c["type"] == "off"]
        total_on_time = sum(on_cycles)
        total_cycle_time = total_on_time + sum(off_cycles)
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

def analyze_compressor_cycles(temperature_series, power_specs, period_info=None):
    """Estimate compressor duty cycle based on temperature thresholds"""
    if len(temperature_series) < 20:
        return {
            "estimated_duty_cycle": 0.4, "cycle_count": 0,
            "avg_on_duration_minutes": 0, "avg_off_duration_minutes": 0,
            "confidence": 0.1, "analysis_period": period_info or "insufficient_data"
        }

    temperatures = [point["value"] for point in temperature_series]
    timestamps = [point["timestamp"] for point in temperature_series]
    temp_min, temp_max = 3.5, 4.5

    total_duration_seconds = timestamps[-1] - timestamps[0]
    if total_duration_seconds <= 0: return {"estimated_duty_cycle": 0.0, "confidence": 0.0}

    estimated_on_time_seconds = 0
    on_durations, off_durations = [], []
    cycle_count = 0
    
    # Initial state estimation
    current_state = 'ON' if temperatures[0] >= temp_max else ('OFF' if temperatures[0] <= temp_min else 'OFF')
    current_phase_start_time = timestamps[0]

    for i in range(1, len(temperatures)):
        t_start = timestamps[i-1]
        t_end = timestamps[i]
        interval_duration = t_end - t_start

        if interval_duration <= 0 or interval_duration > 7200: 
            current_phase_start_time = t_end; continue

        temp_start = temperatures[i-1]
        temp_end = temperatures[i]
        
        # Estimate state for interval
        is_on = temp_start >= temp_max or (temp_start > temp_min and temp_end < temp_start and (temp_start - temp_end) > 0.05)
        estimated_state = 'ON' if is_on else 'OFF'

        if estimated_state == 'ON': estimated_on_time_seconds += interval_duration

        if estimated_state != current_state:
            phase_min = (t_end - current_phase_start_time) / 60.0
            if phase_min >= 2.0:
                if current_state == 'ON': on_durations.append(phase_min)
                else: 
                    off_durations.append(phase_min)
                    if estimated_state == 'ON': cycle_count += 1
            current_state = estimated_state
            current_phase_start_time = t_end

    duty_cycle = max(0.05, min(1.0, estimated_on_time_seconds / total_duration_seconds))
    avg_on = np.mean(on_durations) if on_durations else 0
    avg_off = np.mean(off_durations) if off_durations else 0
    
    # Confidence calculation
    conf = (min(1.0, len(temperature_series)/200) * 0.4 + 
            min(1.0, total_duration_seconds/86400) * 0.4 + 
            min(1.0, cycle_count/5) * 0.2) * 0.9 + 0.1

    return {
        "estimated_duty_cycle": round(duty_cycle, 3),
        "cycle_count": cycle_count,
        "avg_on_duration_minutes": round(avg_on, 1),
        "avg_off_duration_minutes": round(avg_off, 1),
        "confidence": round(min(1.0, conf), 2)
    }

def estimate_daily_energy_consumption(temp_analysis, usage_analysis, cycle_analysis, power_specs):
    base_duty_cycle = cycle_analysis["estimated_duty_cycle"]
    daily_openings = usage_analysis.get("avg_daily_openings", 0)
    avg_duration_min = usage_analysis.get("avg_duration_seconds", 0) / 60.0
    recovery_mult = power_specs.get("recovery_time_multiplier", 1.5)
    
    daily_door_penalty_hours = (daily_openings * avg_duration_min * recovery_mult)
    base_daily_runtime_hours = 24 * base_duty_cycle
    daily_door_penalty_hours = min(daily_door_penalty_hours, base_daily_runtime_hours * 0.5)
    
    stability_score = temp_analysis.get("stability_score", 80)
    temp_factor = 1.2 if stability_score < 70 else (0.9 if stability_score > 90 else 1.0)
    
    total_daily_runtime = max(2.0, min(24.0, (base_daily_runtime_hours + daily_door_penalty_hours) * temp_factor))
    compressor_power = power_specs.get("base_power_watts", 120)
    daily_kwh = (total_daily_runtime * compressor_power) / 1000
    
    return {
        "daily_kwh": round(daily_kwh, 3), 
        "runtime_hours_per_day": round(total_daily_runtime, 2),
        "base_duty_cycle": round(base_duty_cycle, 3),
        "door_penalty_hours": round(daily_door_penalty_hours, 2),
        "cycle_analysis": cycle_analysis
    }

def generate_recommendations(temp_analysis, usage_analysis, energy_estimate, power_specs):
    recommendations = []
    compressor_power = power_specs.get("base_power_watts", 120)
    daily_openings = usage_analysis.get("avg_daily_openings", 0)
    max_eff_openings = power_specs.get("max_efficient_openings_per_day", 15)

    if daily_openings > max_eff_openings:
        reduced = daily_openings - max_eff_openings
        avg_dur_sec = usage_analysis.get("avg_duration_seconds", 30)
        rec_mult = power_specs.get("recovery_time_multiplier", 1.5)
        kwh_save = ((reduced * (avg_dur_sec/60.0) * rec_mult) * compressor_power) / 1000
        recommendations.append({
            "type": "behavioral", "priority": "medium",
            "message": f"Reduce door openings: {daily_openings:.1f}/day. Target: <{max_eff_openings}/day",
            "potential_savings_kwh_day": round(kwh_save, 3)
        })
    
    if temp_analysis.get("stability_score", 80) < 70:
        kwh_save = (energy_estimate["runtime_hours_per_day"] * 0.10 * compressor_power) / 1000
        recommendations.append({
            "type": "maintenance", "priority": "medium",
            "message": f"Unstable temperature. Check door seals.",
            "potential_savings_kwh_day": round(kwh_save, 3)
        })

    if energy_estimate["base_duty_cycle"] > 0.6:
        recommendations.append({
            "type": "alert", "priority": "high",
            "message": f"High duty cycle ({energy_estimate['base_duty_cycle']*100:.1f}%). Possible malfunction."
        })
        
    return recommendations

# ===================== ML Training Logic =====================

def prepare_and_train_model(hist_temp, hist_events, min_samples=5):
    """Prepares data and trains LinearRegression model"""
    features = []
    targets = []
    daily_data = group_data_by_day(hist_temp, hist_events)
    valid_days_count = 0

    for day_str, data in daily_data.items():
        if len(data["temp_points"]) < 48: continue # ~4 hours data min

        day_dt = datetime.strptime(day_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if day_dt.date() >= datetime.now(timezone.utc).date(): continue # Skip today

        # Compute stats for features
        temp_stats = analyze_temperature_data(data["temp_points"], "1d")
        usage_stats = analyze_door_usage(data["door_events"], "1d")
        
        # Compute target (Runtime)
        runtime_target = calculate_historical_runtime(data["temp_points"])
        
        if runtime_target["runtime_hours"] is not None:
            features.append({
                "avg_temperature": temp_stats.get("avg_temperature", 4.0),
                "temperature_variance": temp_stats.get("temperature_variance", 0.5),
                "stability_score": temp_stats.get("stability_score", 80),
                "daily_openings": usage_stats.get("avg_daily_openings", 0),
                "avg_door_duration": usage_stats.get("avg_duration_seconds", 0),
                "day_of_week": day_dt.weekday(),
            })
            targets.append(runtime_target["runtime_hours"])
            valid_days_count += 1

    if valid_days_count < min_samples:
        return None, 0, 0, 0

    feature_names = sorted(list(features[0].keys()))
    X = np.array([[f[name] for name in feature_names] for f in features])
    y = np.array(targets)
    
    model = LinearRegression()
    model.fit(X, y)
    y_pred = model.predict(X)
    
    return model, feature_names, mean_absolute_error(y, y_pred), r2_score(y, y_pred)