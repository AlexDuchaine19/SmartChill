import json
import os
import time
from datetime import datetime, timezone

SETTINGS_FILE = "settings.json"

def load_settings():
    """Load settings from JSON file"""
    if not os.path.exists(SETTINGS_FILE):
        raise FileNotFoundError(f"Settings file not found: {SETTINGS_FILE}")
    
    with open(SETTINGS_FILE, 'r') as f:
        return json.load(f)

def save_settings(settings):
    """Save updated settings back to JSON file"""
    settings["last_config_sync"] = datetime.now(timezone.utc).isoformat()
    
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=4)
    
    print(f"[CONFIG] Settings saved to {SETTINGS_FILE}")

def create_senml_payload(device_id, sensor_type, value, unit, timestamp=None):
    """Create SenML formatted payload for sensor data"""
    if timestamp is None:
        timestamp = time.time()
    
    base_name = f"{device_id}/"
    
    senml_data = {
        "bn": base_name,
        "bt": timestamp,
        "e": [{
            "n": sensor_type,
            "v": round(value, 2),
            "u": unit,
            "t": 0
        }]
    }
    
    return senml_data

def create_door_event_senml_payload(device_id, event_type, duration=None, timestamp=None):
    """Create SenML formatted payload for door events"""
    if timestamp is None:
        timestamp = time.time()
    
    base_name = f"{device_id}/"
    
    event_entry = {
        "n": "door_state",
        "vs": event_type,
        "t": 0
    }
    
    entries = [event_entry]
    if event_type == "door_closed" and duration is not None:
        entries.append({
            "n": "door_duration",
            "v": round(duration, 1),
            "u": "s",
            "t": 0
        })
    
    senml_data = {
        "bn": base_name,
        "bt": timestamp,
        "e": entries
    }
    
    return senml_data

def get_sensor_unit(sensor_type):
    """Get unit for sensor type"""
    units = {
        "temperature": "Cel",
        "humidity": "%RH",
        "light": "lx",
        "gas": "ppm"
    }
    return units.get(sensor_type, "")
