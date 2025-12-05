import numpy as np
from datetime import datetime
from collections import defaultdict

# ===================== Analysis Algorithms =====================

def period_to_days(period):
    """Convert period string to number of days"""
    if period.endswith("d"):
        return int(period[:-1])
    elif period.endswith("h"):
        return int(period[:-1]) / 24
    elif period.endswith("m"):
        return int(period[:-1]) / (24 * 60)
    else:
        return 7

def analyze_temperature_data(temp_data, period):
    """Analyze temperature data and return temperature metrics"""
    if not temp_data:
        return {
            "avg_temperature": 0,
            "min_temperature": 0,
            "max_temperature": 0,
            "temperature_variance": 0,
            "stability_score": 0,
            "out_of_range_time_percent": 100,
            "data_points": 0
        }
    
    temperatures = [point["value"] for point in temp_data]
    
    # Basic statistics
    avg_temp = np.mean(temperatures)
    min_temp = np.min(temperatures)
    max_temp = np.max(temperatures)
    temp_variance = np.var(temperatures)
    temp_std = np.std(temperatures)
    
    # Temperature range analysis (ideal range 2-6°C)
    optimal_range = [2.0, 6.0]
    out_of_range_count = sum(1 for t in temperatures 
                            if t < optimal_range[0] or t > optimal_range[1])
    out_of_range_percent = (out_of_range_count / len(temperatures)) * 100
    
    # Stability score (based on standard deviation)
    if temp_std < 0.5:
        stability_score = 95
    elif temp_std < 1.0:
        stability_score = 85
    elif temp_std < 1.5:
        stability_score = 75
    elif temp_std < 2.0:
        stability_score = 65
    else:
        stability_score = max(0, 60 - (temp_std - 2.0) * 10)
    
    return {
        "avg_temperature": round(avg_temp, 2),
        "min_temperature": round(min_temp, 2),
        "max_temperature": round(max_temp, 2),
        "temperature_variance": round(temp_variance, 3),
        "stability_score": round(stability_score, 1),
        "out_of_range_time_percent": round(out_of_range_percent, 1),
        "data_points": len(temp_data)
    }

def analyze_door_usage(door_events, period):
    """Analyze door usage patterns and return usage metrics"""
    if not door_events:
        return {
            "total_openings": 0,
            "avg_daily_openings": 0,
            "avg_duration_seconds": 0,
            "max_duration_seconds": 0,
            "efficiency_score": 0,
            "events_analyzed": 0
        }
    
    # Filter door_closed events with valid duration
    closed_events = [event for event in door_events 
                    if event.get("event_type") == "door_closed" 
                    and event.get("duration") is not None
                    and isinstance(event.get("duration"), (int, float))]
    
    if not closed_events:
        return {
            "total_openings": len(door_events),
            "avg_daily_openings": 0,
            "avg_duration_seconds": 0,
            "max_duration_seconds": 0,
            "efficiency_score": 0,
            "events_analyzed": len(door_events)
        }
    
    # Calculate duration statistics
    durations = [event["duration"] for event in closed_events]
    avg_duration = np.mean(durations)
    max_duration = np.max(durations)
    
    # Calculate daily average
    days = period_to_days(period)
    avg_daily_openings = len(closed_events) / days if days > 0 else 0
    
    # Calculate efficiency score
    efficiency_score = 100
    
    if avg_daily_openings > 15:
        efficiency_score -= min(30, (avg_daily_openings - 15) * 2)
    
    if avg_duration > 60:
        efficiency_score -= min(40, (avg_duration - 60) / 5)
    
    if max_duration > 180:
        efficiency_score -= min(20, (max_duration - 180) / 10)
    
    efficiency_score = max(0, efficiency_score)
    
    return {
        "total_openings": len(closed_events),
        "avg_daily_openings": round(avg_daily_openings, 1),
        "avg_duration_seconds": round(avg_duration, 1),
        "max_duration_seconds": round(max_duration, 1),
        "efficiency_score": round(efficiency_score, 1),
        "events_analyzed": len(door_events)
    }

def analyze_trends(temp_data, door_events, period):
    """Analyze trends in temperature and usage data"""
    trends = {
        "temperature_trend": "stable",
        "usage_trend": "stable",
        "period_analyzed": period
    }
    
    # Temperature trend analysis
    if temp_data and len(temp_data) > 10:
        temperatures = [point["value"] for point in temp_data]
        x = np.arange(len(temperatures))
        slope = np.polyfit(x, temperatures, 1)[0]
        
        if slope > 0.05:
            trends["temperature_trend"] = "increasing"
        elif slope < -0.05:
            trends["temperature_trend"] = "decreasing"
        else:
            trends["temperature_trend"] = "stable"
    
    # Usage trend analysis
    if door_events and len(door_events) > 5:
        daily_counts = defaultdict(int)
        
        for event in door_events:
            if event.get("timestamp"):
                try:
                    event_time = datetime.fromtimestamp(event["timestamp"])
                    day_key = event_time.strftime("%Y-%m-%d")
                    daily_counts[day_key] += 1
                except:
                    continue
        
        if len(daily_counts) > 3:
            daily_values = list(daily_counts.values())
            x = np.arange(len(daily_values))
            slope = np.polyfit(x, daily_values, 1)[0]
            
            if slope > 0.5:
                trends["usage_trend"] = "increasing"
            elif slope < -0.5:
                trends["usage_trend"] = "decreasing"
            else:
                trends["usage_trend"] = "stable"
    
    return trends