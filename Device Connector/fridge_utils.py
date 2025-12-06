import json
import os
import time
from datetime import datetime, timezone

# ===================== Utilities =====================

def load_settings(file_path):
    """Load settings from JSON file"""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Settings file not found: {file_path}")
    
    with open(file_path, 'r') as f:
        return json.load(f)

def save_settings_to_file(settings, file_path):
    """Save settings dictionary to JSON file"""
    try:
        with open(file_path, 'w') as f:
            json.dump(settings, f, indent=4)
        print(f"[CONFIG] Settings saved to {file_path}")
    except Exception as e:
        print(f"[ERROR] Failed to save settings: {e}")

def build_topic(template, model, device_id, sensor_or_event):
    """Build MQTT topic using template"""
    if not device_id:
        return None
        
    return template.format(
        model=model,
        device_id=device_id,
        sensor=sensor_or_event
    )

def build_heartbeat_topic(template, model, device_id):
    """Build heartbeat topic using template"""
    if not device_id:
        return None
        
    return template.format(
        model=model,
        device_id=device_id
    )

def build_command_topic(device_id, command_type):
    """Build command topic for receiving simulation commands"""
    if not device_id:
        return None
    return f"Group17/SmartChill/Commands/{device_id}/{command_type}"

def build_response_topic(device_id):
    """Build response topic for sending command confirmations"""
    if not device_id:
        return None
    return f"Group17/SmartChill/Response/{device_id}/command_result"

def get_sensor_unit(sensor_type):
    """Get unit for sensor type"""
    units = {
        "temperature": "Cel",  # celsius
        "humidity": "%RH",     # relative humidity
        "light": "lx",         # lux
        "gas": "ppm"           # Parts per million
    }
    return units.get(sensor_type, "")

def create_senml_payload(device_id, sensor_type, value, timestamp=None):
    """Create SenML formatted payload for sensor data"""
    if timestamp is None:
        timestamp = time.time()
    
    # SenML base name for device identification
    base_name = f"{device_id}/"
    
    # Get sensor unit
    unit = get_sensor_unit(sensor_type)
    
    # Create SenML object
    senml_data = {
        "bn": base_name,  # Base name
        "bt": timestamp,  # Base time
        "e": [{
            "n": sensor_type,  # Sensor name
            "v": round(value, 2),  # Value
            "u": unit,  # Unit
            "t": 0  # Time offset from base time
        }]
    }
    
    return senml_data

def create_door_event_senml_payload(device_id, event_type, duration=None, timestamp=None):
    """Create SenML formatted payload for door events"""
    if timestamp is None:
        timestamp = time.time()
    
    base_name = f"{device_id}/"
    
    # Create basic event entry
    event_entry = {
        "n": "door_state",
        "vs": event_type,  # String value for event type
        "t": 0
    }
    
    # Add duration if door is closing
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

def get_door_open_probability():
    """
    Calculate the probability of the door opening based on the time of day.
    """
    now = datetime.now()
    current_hour = now.hour
    # Monday is 0 and Sunday is 6
    is_weekend = now.weekday() >= 5

    # (start_hour, end_hour, probability)
    weekday_schedule = [
        (7, 9, 0.001),     # Breakfast
        (12, 14, 0.0025),  # Lunch
        (19, 21, 0.0025)   # Dinner
    ]
    
    weekend_schedule = [
        (9, 11, 0.0015),   # Late breakfast
        (13, 15, 0.003),   # Lunch
        (19, 22, 0.003)    # Dinner
    ]

    # Use the right schedule
    schedule = weekend_schedule if is_weekend else weekday_schedule
    
    # Check if the current time falls into a scheduled high-activity period
    for start, end, prob in schedule:
        if start <= current_hour < end:
            return prob
    
    # Low base probability for off-peak hours
    if 0 <= current_hour < 6:
        return 0.00001
    else:
        return 0.0001