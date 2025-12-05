import json
from datetime import datetime, timezone

# ===================== Data Parsing =====================

def parse_senml_payload(payload):
    """Parse SenML formatted payload and extract sensor data"""
    try:
        # Decode se payload è bytes
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")

        # Parse JSON
        senml_data = json.loads(payload) if isinstance(payload, str) else payload
        
        # Validate SenML structure
        if not isinstance(senml_data, dict) or "e" not in senml_data:
            print(f"[SENML] Invalid SenML structure - missing 'e' array")
            return None
        
        base_name = senml_data.get("bn", "")
        base_time = senml_data.get("bt", 0)
        entries = senml_data.get("e", [])
        
        # Extract device_id from base_name (format: "device_id/")
        device_id = base_name.rstrip("/") if base_name.endswith("/") else None
        
        parsed_data = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            
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
    """Validate configuration values and return error message if invalid"""
    
    # Check gas_threshold_ppm
    if "gas_threshold_ppm" in config:
        value = config["gas_threshold_ppm"]
        if not isinstance(value, int) or value < 100 or value > 1000:
            return "gas_threshold_ppm must be an integer between 100 and 1000"
    
    # Check enable_continuous_alerts
    if "enable_continuous_alerts" in config:
        value = config["enable_continuous_alerts"]
        if not isinstance(value, bool):
            return "enable_continuous_alerts must be a boolean (true/false)"
    
    # Check alert_cooldown_minutes
    if "alert_cooldown_minutes" in config:
        value = config["alert_cooldown_minutes"]
        if not isinstance(value, int) or value < 5 or value > 120:
            return "alert_cooldown_minutes must be an integer between 5 and 120"
    
    # Check for unknown config keys
    allowed_keys = {"gas_threshold_ppm", "enable_continuous_alerts", "alert_cooldown_minutes"}
    unknown_keys = set(config.keys()) - allowed_keys
    if unknown_keys:
        return f"Unknown configuration keys: {', '.join(unknown_keys)}"
    
    return None  # No validation errors

# ===================== Alert Logic =====================

def check_alert_condition(gas_value, threshold):
    """Determine if gas level is high or normal"""
    return "high" if gas_value > threshold else "normal"

def should_trigger_alert(current_status, previous_status, enable_continuous, is_cooldown_active):
    """
    Decide if an alert should be triggered based on status transition 
    and configuration.
    """
    if current_status == "high":
        if enable_continuous:
            # Send alert if not in cooldown
            if not is_cooldown_active:
                return True
        else:
            # Send alert only on transition from normal to high
            if previous_status == "normal":
                return True
    return False

def format_timestamp(ts):
    """Helper to ensure timestamp is isoformat string"""
    if isinstance(ts, datetime):
        return ts.isoformat()
    return ts # Assume already formatted or numeric if handled downstream