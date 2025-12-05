import json
from datetime import datetime, timezone
from influxdb_client import Point, WritePrecision

# ===================== Data Parsing & Utilities =====================

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
            string_value = entry.get("vs")
            time_offset = entry.get("t", 0)
            timestamp = base_time + time_offset
            
            if sensor_name and (value is not None or string_value is not None):
                parsed_data.append({
                    "device_id": device_id,
                    "sensor_name": sensor_name,
                    "value": value,
                    "string_value": string_value,
                    "timestamp": timestamp
                })
        
        return parsed_data
        
    except (json.JSONDecodeError, TypeError) as e:
        print(f"[SENML] Error parsing SenML payload: {e}")
        return None

def validate_sensor_data(device_id, sensor_type, value, enable_validation=True):
    """Validate sensor data against predefined rules"""
    if not enable_validation:
        return True
    
    # Basic validation rules
    validation_rules = {
        "temperature": {"min": -50, "max": 100},  # Celsius
        "humidity": {"min": 0, "max": 100},       # Percentage
        "light": {"min": 0, "max": 100000},       # Lux
        "gas": {"min": 0, "max": 1000}            # PPM or sensor-specific units
    }
    
    if sensor_type in validation_rules:
        rules = validation_rules[sensor_type]
        # Ensure value is numeric for comparison
        if isinstance(value, (int, float)):
            if not (rules["min"] <= value <= rules["max"]):
                print(f"[VALIDATION] Invalid {sensor_type} value from {device_id}: {value}")
                return False
    
    return True

def create_influx_point(measurement_name, device_id, sensor_type, value, timestamp):
    """Create InfluxDB point from sensor data"""
    try:
        point = Point(measurement_name) \
            .tag("device_id", device_id) \
            .tag("sensor_type", sensor_type) \
            .field("value", float(value)) \
            .time(timestamp, WritePrecision.S)
        
        return point
    except Exception as e:
        print(f"[INFLUX] Error creating point: {e}")
        return None

def create_door_event_point(measurement_name, device_id, event_type, value, timestamp, duration=None):
    """Create InfluxDB point for door events"""
    try:
        # Create point for door event
        point = Point(measurement_name) \
            .tag("device_id", device_id) \
            .tag("event_type", event_type) \
            .field("value", value) \
            .time(timestamp, WritePrecision.S)
        
        # Add duration field if provided
        if duration is not None:
            point = point.field("duration_seconds", round(duration, 2))
            
        return point
    except Exception as e:
        print(f"[INFLUX] Error creating door event point: {e}")
        return None