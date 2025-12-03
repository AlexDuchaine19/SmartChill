import json
import os
import threading
from datetime import datetime, timezone

def load_settings(settings_file="settings.json"):
    """Load settings from JSON file"""
    try:
        with open(settings_file, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"[ERROR] Settings file {settings_file} not found")
        raise
    except json.JSONDecodeError as e:
        print(f"[ERROR] Invalid JSON in settings file: {e}")
        raise

def save_settings(settings, settings_file="settings.json", lock=None):
    """Save current settings to file"""
    if lock:
        with lock:
            _write_settings(settings, settings_file)
    else:
        _write_settings(settings, settings_file)

def _write_settings(settings, settings_file):
    settings["lastUpdate"] = datetime.now(timezone.utc).isoformat()
    settings["configVersion"] = settings.get("configVersion", 0) + 1
    
    try:
        with open(settings_file, 'w') as f:
            json.dump(settings, f, indent=4)
        print(f"[CONFIG] Settings saved to {settings_file}")
    except Exception as e:
        print(f"[ERROR] Failed to save settings: {e}")
