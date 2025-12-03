import json
import os
from datetime import datetime, timezone

def load_settings(settings_file="settings.json"):
    """Load settings from JSON file"""
    if not os.path.exists(settings_file):
        raise FileNotFoundError(f"Settings file not found: {settings_file}")
    
    with open(settings_file, 'r') as f:
        return json.load(f)

def save_settings(settings, settings_file="settings.json"):
    """Save updated settings back to JSON file"""
    settings["last_config_sync"] = datetime.now(timezone.utc).isoformat()
    
    with open(settings_file, 'w') as f:
        json.dump(settings, f, indent=4)
    
    print(f"[CONFIG] Settings saved to {settings_file}")
