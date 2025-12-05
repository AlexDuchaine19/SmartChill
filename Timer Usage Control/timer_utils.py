import json
import time
from datetime import datetime

# ===================== Data Parsing =====================

def parse_senml_door_event(payload):
    """Parse SenML payload and extract door event data"""
    try:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")

        senml_data = json.loads(payload) if isinstance(payload, str) else payload
        
        if not isinstance(senml_data, dict) or "e" not in senml_data:
            print(f"[SENML] Invalid SenML structure")
            return None
        
        base_name = senml_data.get("bn", "")
        base_time = senml_data.get("bt", 0)
        entries = senml_data.get("e", [])
        
        device_id = base_name.rstrip("/") if base_name.endswith("/") else None
        
        event_data = {}
        for entry in entries:
            if not isinstance(entry, dict): continue
            
            name = entry.get("n")
            time_offset = entry.get("t", 0)
            
            # Door State
            if name == "door_state":
                event_data["event_type"] = entry.get("vs")
                event_data["timestamp"] = base_time + time_offset
                event_data["device_id"] = device_id
            
            # Door Duration
            elif name == "door_duration":
                event_data["door_open_duration"] = entry.get("v")
        
        return event_data if "event_type" in event_data else None
        
    except (json.JSONDecodeError, TypeError) as e:
        print(f"[SENML] Error parsing door event: {e}")
        return None

# ===================== Validation Logic =====================

def validate_config_values(config):
    """Validate configuration values"""
    
    # Check max_door_open_seconds
    if "max_door_open_seconds" in config:
        val = config["max_door_open_seconds"]
        if not isinstance(val, int) or val < 30 or val > 300:
            return "max_door_open_seconds must be int between 30 and 300"
    
    # Check check_interval
    if "check_interval" in config:
        val = config["check_interval"]
        if not isinstance(val, int) or val < 1 or val > 30:
            return "check_interval must be int between 1 and 30"
    
    # Check enable_door_closed_alerts
    if "enable_door_closed_alerts" in config:
        if not isinstance(config["enable_door_closed_alerts"], bool):
            return "enable_door_closed_alerts must be boolean"
    
    allowed = {"max_door_open_seconds", "check_interval", "enable_door_closed_alerts"}
    unknown = set(config.keys()) - allowed
    if unknown: return f"Unknown keys: {', '.join(unknown)}"
    
    return None

# ===================== Evaluation Logic =====================

def check_timeout_condition(duration, threshold):
    """Check if door open duration exceeds threshold"""
    return duration >= threshold

def calculate_duration(start_time, end_time=None):
    """Calculate duration in seconds"""
    if end_time is None:
        end_time = time.time()
    return end_time - start_time