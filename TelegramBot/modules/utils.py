import json
import re
import os

SETTINGS_FILE = "settings.json"

def normalize_mac(s: str) -> str:
    """Removes separators and uppercases a MAC address string."""
    return re.sub(r"[^0-9A-Fa-f]", "", (s or "")).upper()

def is_valid_mac(s: str) -> bool:
    """Checks if a string is a valid 12-character hex MAC address."""
    return len(normalize_mac(s)) == 12

def is_valid_username(s: str) -> bool:
    """Checks if a string is a valid username (3-32 chars: letters, numbers, _, ., -)."""
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", s or ""))

def load_settings(filename=SETTINGS_FILE):
    """Load settings from JSON file with basic validation."""
    try:
        with open(filename, 'r') as f:
            settings_data = json.load(f)
            
            # Basic validation to ensure critical fields exist
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
    Used by bot handlers for configuration menus.
    """
    settings_map = {
        # --- Timer Usage Control ---
        "max_door_open_seconds": {
            "name": "Door Open Timeout",
            "desc": "The maximum time (in seconds) the fridge door can stay open before an alert is triggered.",
            "range_text": "(30 - 300 seconds)",
            "min": 30, "max": 300, "type": int
        },
        "check_interval": {
            "name": "Check Interval",
            "desc": "How often (in seconds) the system checks the door status.",
            "range_text": "(1 - 30 seconds)",
            "min": 1, "max": 30, "type": int
        },
        "enable_door_closed_alerts": {
            "name": "Door Closed Notifications",
            "desc": "Receive a notification when the door is closed after being left open.",
            "range_text": "(Enabled/Disabled)",
            "type": bool,
            "true_text": "Enabled",
            "false_text": "Disabled"
        },

        # --- Food Spoilage Control ---
        "gas_threshold_ppm": {
            "name": "Gas Level Threshold",
            "desc": "The concentration of gas (PPM) that indicates potential food spoilage.",
            "range_text": "(100 - 1000 PPM)",
            "min": 100, "max": 1000, "type": int
        },
        "alert_cooldown_minutes": {
            "name": "Alert Cooldown",
            "desc": "Wait time (in minutes) before sending another alert for the same issue.",
            "range_text": "(5 - 120 minutes)",
            "min": 5, "max": 120, "type": int
        },
        "enable_continuous_alerts": {
            "name": "Continuous Alerts",
            "desc": "If enabled, alerts repeat while the gas level remains high. If disabled, you only get one alert per breach.",
            "range_text": "(Enabled/Disabled)",
            "type": bool,
            "true_text": "Continuous",
            "false_text": "One-time"
        },

        # --- Fridge Status Control ---
        "temp_min_celsius": {
            "name": "Min Temperature",
            "desc": "The lowest acceptable temperature. Below this, food might freeze.",
            "range_text": "(-5 to 5 °C)",
            "min": -5, "max": 5, "type": float
        },
        "temp_max_celsius": {
            "name": "Max Temperature",
            "desc": "The highest acceptable temperature. Above this, food might spoil.",
            "range_text": "(5 to 15 °C)",
            "min": 5, "max": 15, "type": float
        },
        "humidity_max_percent": {
            "name": "Max Humidity",
            "desc": "The maximum humidity percentage allowed before warning about ice/mold risk.",
            "range_text": "(50 - 95 %)",
            "min": 50, "max": 95, "type": float
        },
        "enable_malfunction_alerts": {
            "name": "Malfunction Alerts",
            "desc": "Enable or disable alerts for temperature and humidity issues.",
            "range_text": "(Enabled/Disabled)",
            "type": bool,
            "true_text": "Enabled", 
            "false_text": "Disabled"
        }
    }
    
    # Returns details or a generic dictionary if the key is not found
    return settings_map.get(field_name, {
        "name": field_name.replace('_', ' ').title(), 
        "desc": "Configuration parameter.", 
        "range_text": "", 
        "type": "unknown"
    })