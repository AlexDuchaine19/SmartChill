import json
import os
import numpy as np

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

def period_to_days(period):
    """Convert period string (e.g., '7d', '24h') to days"""
    if isinstance(period, (int, float)): return period
    if isinstance(period, str):
        if period.endswith("d"): return int(period[:-1])
        elif period.endswith("h"): return int(period[:-1]) / 24
        elif period.endswith("w"): return int(period[:-1]) * 7
    return 7 # Default fallback
