import json
from datetime import datetime

# ===================== Data Parsing =====================

def parse_senml_payload(payload):
    """Parse SenML formatted payload and extract sensor data"""
    try:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")

        senml_data = json.loads(payload) if isinstance(payload, str) else payload
        
        if not isinstance(senml_data, dict) or "e" not in senml_data:
            print(f"[SENML] Invalid SenML structure - missing 'e' array")
            return None
        
        base_name = senml_data.get("bn", "")
        base_time = senml_data.get("bt", 0)
        entries = senml_data.get("e", [])
        
        device_id = base_name.rstrip("/") if base_name.endswith("/") else None
        
        parsed_data = []
        for entry in entries:
            if not isinstance(entry, dict): continue
            
            sensor_name = entry.get("n")
            value = entry.get("v")
            time_offset = entry.get("t", 0)
            timestamp = base_time + time_offset
            
            if sensor_name and value is not None:
                parsed_data.append({
                    "device_id": device_id,
                    "sensor_name": sensor_name,
                    "value": value,
                    "timestamp": timestamp
                })
        return parsed_data
    except (json.JSONDecodeError, TypeError) as e:
        print(f"[SENML] Error parsing SenML payload: {e}")
        return None

# ===================== Validation Logic =====================

def validate_config_values(config):
    """Validate configuration values"""
    
    # Check temp_min_celsius
    if "temp_min_celsius" in config:
        value = config["temp_min_celsius"]
        if not isinstance(value, (int, float)) or value < -5.0 or value > 5.0:
            return "temp_min_celsius must be a number between -5.0 and 5.0"
    
    # Check temp_max_celsius
    if "temp_max_celsius" in config:
        value = config["temp_max_celsius"]
        if not isinstance(value, (int, float)) or value < 5.0 or value > 15.0:
            return "temp_max_celsius must be a number between 5.0 and 15.0"
    
    # Check that temp_min < temp_max
    temp_min = config.get("temp_min_celsius")
    temp_max = config.get("temp_max_celsius")
    if temp_min is not None and temp_max is not None:
        if temp_min >= temp_max:
            return "temp_min_celsius must be less than temp_max_celsius"
    
    # Check humidity_max_percent
    if "humidity_max_percent" in config:
        value = config["humidity_max_percent"]
        if not isinstance(value, (int, float)) or value < 50.0 or value > 95.0:
            return "humidity_max_percent must be a number between 50.0 and 95.0"
    
    # Check boolean/int fields
    if "enable_malfunction_alerts" in config and not isinstance(config["enable_malfunction_alerts"], bool):
        return "enable_malfunction_alerts must be a boolean"
    
    if "alert_cooldown_minutes" in config:
        val = config["alert_cooldown_minutes"]
        if not isinstance(val, int) or val < 5 or val > 120:
            return "alert_cooldown_minutes must be an integer between 5 and 120"
    
    allowed = {
        "temp_min_celsius", "temp_max_celsius", "humidity_max_percent", 
        "enable_malfunction_alerts", "alert_cooldown_minutes"
    }
    unknown = set(config.keys()) - allowed
    if unknown: return f"Unknown keys: {', '.join(unknown)}"
    
    return None

# ===================== Evaluation Logic =====================

def evaluate_temperature(temperature, temp_min, temp_max):
    """Evaluate temperature against thresholds"""
    if temperature < temp_min:
        return {
            "status": "too_low",
            "alert_type": "temperature_too_low",
            "message": f"Temperature too low: {temperature:.1f}°C (min: {temp_min}°C). Risk of freezing.",
            "action": "Check thermostat settings and increase temperature",
            "severity": "warning"
        }
    elif temperature > temp_max:
        return {
            "status": "too_high",
            "alert_type": "temperature_too_high",
            "message": f"Temperature too high: {temperature:.1f}°C (max: {temp_max}°C). Risk of spoilage.",
            "action": "Check thermostat, door seals, reduce temp",
            "severity": "critical"
        }
    return {"status": "normal", "alert_type": None}

def evaluate_humidity(humidity, humidity_max):
    """Evaluate humidity against thresholds"""
    if humidity > humidity_max:
        return {
            "status": "too_high",
            "alert_type": "humidity_too_high",
            "message": f"Humidity too high: {humidity:.1f}% (max: {humidity_max}%). Risk of ice/condensation.",
            "action": "Check door seals, defrost, air circulation",
            "severity": "warning"
        }
    return {"status": "normal", "alert_type": None}

def evaluate_complex_patterns(temperature, humidity, config):
    """Detect complex malfunction patterns"""
    if temperature > config["temp_max_celsius"] and humidity > config["humidity_max_percent"]:
        return {
            "alert_type": "cooling_system_failure",
            "message": f"Possible cooling failure: High temp ({temperature:.1f}°C) & humidity ({humidity:.1f}%)",
            "action": "Check cooling system, compressor. Contact technician.",
            "severity": "critical"
        }
    elif temperature < config["temp_min_celsius"] and humidity > config["humidity_max_percent"]:
        return {
            "alert_type": "defrost_cycle_issue",
            "message": f"Possible defrost issue: Low temp ({temperature:.1f}°C) with high humidity ({humidity:.1f}%)",
            "action": "Check defrost cycle settings and drainage",
            "severity": "warning"
        }
    return None

def format_timestamp(ts):
    if isinstance(ts, datetime): return ts.isoformat()
    return ts