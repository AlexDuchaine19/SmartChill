import numpy as np
from modules.utils import period_to_days

class EnergyAnalyzer:
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

    def analyze_door_usage(self, door_events, period):
        if not door_events: return {"avg_daily_openings": 0, "avg_duration_seconds": 0}
        closed_events = [e for e in door_events if e.get("event_type") == "door_closed" and e.get("duration") is not None]
        if not closed_events: return {"avg_daily_openings": 0, "avg_duration_seconds": 0}
        durations = [e["duration"] for e in closed_events]; avg_duration = np.mean(durations) if durations else 0
        period_days = period_to_days(period); avg_daily_openings = len(closed_events) / period_days if period_days > 0 else len(closed_events)
        return {"total_openings": len(closed_events), "avg_daily_openings": round(avg_daily_openings, 1), "avg_duration_seconds": round(avg_duration, 1)}

    def analyze_compressor_cycles(self, temperature_series, power_specs, period_info=None):
        if len(temperature_series) < 20:
            print(f"[CYCLE] Insufficient data ({len(temperature_series)} points). Using fallback.")
            return {
                "estimated_duty_cycle": 0.4, "cycle_count": 0,
                "avg_on_duration_minutes": 0, "avg_off_duration_minutes": 0,
                "confidence": 0.1, "analysis_period": period_info or "insufficient_data"
            }

        temperatures = [point["value"] for point in temperature_series]
        timestamps = [point["timestamp"] for point in temperature_series]
        
        temp_min = 3.5
        temp_max = 4.5

        total_duration_seconds = timestamps[-1] - timestamps[0]
        if total_duration_seconds <= 0:
             return {
                "estimated_duty_cycle": 0.0, "cycle_count": 0,
                "avg_on_duration_minutes": 0, "avg_off_duration_minutes": 0,
                "confidence": 0.1, "analysis_period": period_info or "invalid_duration"
             }

        estimated_on_time_seconds = 0
        on_durations = []
        off_durations = []
        cycle_count = 0
        current_state = None
        current_phase_start_time = timestamps[0]

        if temperatures[0] >= temp_max: current_state = 'ON'
        elif temperatures[0] <= temp_min: current_state = 'OFF'
        elif len(temperatures) > 1 and temperatures[1] < temperatures[0]: current_state = 'ON'
        else: current_state = 'OFF'

        for i in range(1, len(temperatures)):
            t_start = timestamps[i-1]; t_end = timestamps[i]
            temp_start = temperatures[i-1]; temp_end = temperatures[i]
            interval_duration_seconds = t_end - t_start

            if interval_duration_seconds <= 0 or interval_duration_seconds > 3600 * 2:
                current_phase_start_time = t_end
                if temp_end >= temp_max: current_state = 'ON'
                elif temp_end <= temp_min: current_state = 'OFF'
                continue

            compressor_likely_on_this_interval = False
            if temp_start >= temp_max: compressor_likely_on_this_interval = True
            elif temp_start > temp_min and temp_end < temp_start and (temp_start - temp_end) > 0.05: compressor_likely_on_this_interval = True
            
            estimated_state_this_interval = 'ON' if compressor_likely_on_this_interval else 'OFF'

            if estimated_state_this_interval == 'ON': estimated_on_time_seconds += interval_duration_seconds

            if estimated_state_this_interval != current_state:
                phase_duration_minutes = (t_end - current_phase_start_time) / 60.0
                if phase_duration_minutes >= 2.0:
                    if current_state == 'ON': on_durations.append(phase_duration_minutes)
                    else:
                        off_durations.append(phase_duration_minutes)
                        if estimated_state_this_interval == 'ON': cycle_count += 1
                current_state = estimated_state_this_interval
                current_phase_start_time = t_end

        final_phase_duration_minutes = (timestamps[-1] - current_phase_start_time) / 60.0
        if final_phase_duration_minutes >= 2.0:
            if current_state == 'ON': on_durations.append(final_phase_duration_minutes)
            else:
                 off_durations.append(final_phase_duration_minutes)
                 if cycle_count == 0 and len(on_durations) > 0 and len(off_durations) > 0:
                      if on_durations[0] >= 2.0 and off_durations[0] >= 2.0: cycle_count = 1

        estimated_duty_cycle = estimated_on_time_seconds / total_duration_seconds
        estimated_duty_cycle = max(0.0, min(1.0, estimated_duty_cycle))
        if estimated_on_time_seconds > 0 and estimated_duty_cycle < 0.05: estimated_duty_cycle = 0.05

        avg_on_duration = np.mean(on_durations) if on_durations else 0
        avg_off_duration = np.mean(off_durations) if off_durations else 0

        points_factor = min(1.0, len(temperature_series) / 200.0)
        duration_hours = total_duration_seconds / 3600.0
        duration_factor = min(1.0, duration_hours / 24.0)
        cycle_factor = min(1.0, cycle_count / 5.0)
        confidence = (points_factor * 0.4 + duration_factor * 0.4 + cycle_factor * 0.2) * 0.9 + 0.1
        confidence = min(1.0, confidence)

        return {
            "estimated_duty_cycle": round(estimated_duty_cycle, 3),
            "cycle_count": cycle_count,
            "avg_on_duration_minutes": round(avg_on_duration, 1) if avg_on_duration > 0 else 0,
            "avg_off_duration_minutes": round(avg_off_duration, 1) if avg_off_duration > 0 else 0,
            "confidence": round(confidence, 2),
            "analysis_period": period_info or f"{duration_hours:.1f}h"
        }

    def estimate_daily_energy_consumption(self, device_id, temp_analysis, usage_analysis, cycle_analysis, power_specs):
        base_duty_cycle = cycle_analysis["estimated_duty_cycle"]
        daily_openings = usage_analysis.get("avg_daily_openings", 0)
        avg_duration_min = usage_analysis.get("avg_duration_seconds", 0) / 60.0
        recovery_mult = power_specs.get("recovery_time_multiplier", 1.5)
        daily_door_penalty_hours = (daily_openings * avg_duration_min * recovery_mult)
        
        stability_score = temp_analysis.get("stability_score", 80)
        temp_factor = 1.2 if stability_score < 70 else (0.9 if stability_score > 90 else 1.0)
        
        base_daily_runtime_hours = 24 * base_duty_cycle
        daily_door_penalty_hours = min(daily_door_penalty_hours, base_daily_runtime_hours * 0.5)
        
        total_daily_runtime_hours = (base_daily_runtime_hours + daily_door_penalty_hours) * temp_factor
        total_daily_runtime_hours = max(2.0, min(24.0, total_daily_runtime_hours))

        compressor_power_watts = power_specs.get("base_power_watts", 120)
        daily_kwh = (total_daily_runtime_hours * compressor_power_watts) / 1000
        
        return {
            "daily_kwh": round(daily_kwh, 3), "runtime_hours_per_day": round(total_daily_runtime_hours, 2),
            "base_duty_cycle": round(base_duty_cycle, 3), "base_runtime_hours": round(base_daily_runtime_hours, 2),
            "door_penalty_hours": round(daily_door_penalty_hours, 2), "temperature_factor": round(temp_factor, 2),
            "compressor_power_watts": compressor_power_watts, "cycle_analysis": cycle_analysis
        }

    def generate_recommendations(self, device_id, temp_analysis, usage_analysis, energy_estimate, power_specs):
        recommendations = []
        compressor_power = power_specs.get("base_power_watts", 120)
        
        daily_openings = usage_analysis.get("avg_daily_openings", 0)
        if daily_openings > power_specs.get("max_efficient_openings_per_day", 15):
            openings_to_reduce = daily_openings - power_specs.get("max_efficient_openings_per_day", 15)
            avg_duration_sec = usage_analysis.get("avg_duration_seconds", 30)
            recovery_mult = power_specs.get("recovery_time_multiplier", 1.5)
            runtime_savings = (openings_to_reduce * (avg_duration_sec / 60.0) * recovery_mult) 
            kwh_savings = (runtime_savings * compressor_power) / 1000
            
            recommendations.append({
                "type": "behavioral", "priority": "medium",
                "message": f"Reduce door openings: {daily_openings:.1f}/day. Target: <{power_specs.get('max_efficient_openings_per_day', 15)}/day",
                "potential_savings_kwh_day": round(kwh_savings, 3),
            })
        
        stability_score = temp_analysis.get("stability_score", 80)
        if stability_score < 70:
            runtime_savings = energy_estimate["runtime_hours_per_day"] * 0.10 
            kwh_savings = (runtime_savings * compressor_power) / 1000
            recommendations.append({
                "type": "maintenance", "priority": "medium",
                "message": f"Unstable temperature (stability: {stability_score:.1f}%). Check door seals.",
                "potential_savings_kwh_day": round(kwh_savings, 3),
            })
        
        if energy_estimate["base_duty_cycle"] > 0.6:
             confidence = energy_estimate["cycle_analysis"].get("confidence", 0)
             confidence_msg = f"(Confidence: {confidence*100:.0f}%)" if confidence < 0.5 else ""
             recommendations.append({
                 "type": "alert", "priority": "high",
                 "message": f"High duty cycle ({energy_estimate['base_duty_cycle']*100:.1f}%). Possible malfunction or overload. {confidence_msg}".strip(),
             })
        
        return recommendations
