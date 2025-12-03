import json
import re

def normalize_mac(s: str) -> str:
    return re.sub(r"[^0-9A-Fa-f]", "", (s or "")).upper()

def is_valid_mac(s: str) -> bool:
    return len(normalize_mac(s)) == 12

def is_valid_username(s: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", s or ""))

def load_settings(filename="settings.json"):
    try:
        with open(filename, 'r') as f:
            settings_data = json.load(f)
            # Basic validation
            if "telegram" not in settings_data or "TOKEN" not in settings_data["telegram"]:
                raise ValueError("Missing 'telegram' or 'TOKEN' in settings.")
            if "catalog" not in settings_data or "url" not in settings_data["catalog"]:
                raise ValueError("Missing 'catalog' or 'url' in settings.")
            if "mqtt" not in settings_data or "brokerIP" not in settings_data["mqtt"] or "brokerPort" not in settings_data["mqtt"]:
                raise ValueError("Missing 'mqtt' config (brokerIP, brokerPort) in settings.")
            return settings_data
    except FileNotFoundError:
        print(f"[ERROR] Settings file '{filename}' not found.")
        raise
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[ERROR] Invalid or incomplete settings file '{filename}': {e}")
        raise

def get_setting_details(field_name):
    """
    Helper function to get validation rules and user-friendly text for a setting.
    """
    settings_map = {
        # Timer Configuration
        "max_door_open_seconds": {
            "name": "Door Open Timeout",
            "desc": "Maximum duration the door can remain open before triggering an alert.",
            "range_text": "(30-300 seconds)",
            "min": 30, "max": 300, "type": int
        },
        "check_interval": {
            "name": "Check Interval",
            "desc": "Frequency of monitoring checks for door violations.",
            "range_text": "(1-30 seconds)",
            "min": 1, "max": 30, "type": int
        },
        # Spoilage Detection
        "gas_threshold_ppm": {
            "name": "Gas Level Threshold",
            "desc": "Gas concentration level that triggers spoilage alerts.",
            "range_text": "(100-1000 PPM)",
            "min": 100, "max": 1000, "type": int
        },
        "alert_cooldown_minutes": {
            "name": "Alert Cooldown Period",
            "desc": "Minimum time between consecutive alerts to prevent spam.",
            "range_text": "(5-120 minutes)",
            "min": 5, "max": 120, "type": int
        },
        "enable_continuous_alerts": {
            "name": "Alert Frequency",
            "desc": "Configure how and when spoilage alerts are triggered.",
            "range_text": "(On Breach Only / Continuous)",
            "type": bool,
            "true_text": "Continuous while above threshold",
            "false_text": "On Breach Only"
        },
        # Status Control
        "temp_min_celsius": {
            "name": "Minimum Temperature",
            "desc": "Acceptable temperature range for proper food preservation.",
            "range_text": "(-5 to 5 °C)",
            "min": -5, "max": 5, "type": float
        },
        "temp_max_celsius": {
            "name": "Maximum Temperature",
            "desc": "Acceptable temperature range for proper food preservation.",
            "range_text": "(5 to 15 °C)",
            "min": 5, "max": 15, "type": float
        },
        "humidity_max_percent": {
            "name": "Humidity Threshold",
            "desc": "Maximum humidity level before triggering malfunction alerts.",
            "range_text": "(50-95 %)",
            "min": 50, "max": 95, "type": float
        },
        "enable_malfunction_alerts": {
            "name": "Malfunction Alerts",
            "desc": "Control when malfunction alerts are sent.",
            "range_text": "(Enabled/Disabled)",
            "type": bool,
            "true_text": "Enabled", 
            "false_text": "Disabled"
        },
        "enable_door_closed_alerts": {
            "name": "Door Closed Alerts",
            "desc": "Send notification when door closes after exceeding timeout.",
            "range_text": "(Enabled/Disabled)",
            "type": bool,
            "true_text": "Enabled",
            "false_text": "Disabled"
        }
    }
    # Returns details or an empty dictionary if not found
    return settings_map.get(field_name, {"name": field_name, "desc": "", "range_text": "", "type": "unknown"})
