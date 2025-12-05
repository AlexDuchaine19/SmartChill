import json
import os
from datetime import datetime, timezone

# ===================== Constants & Configuration =====================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CATALOG_FILE = os.environ.get("CATALOG_FILE", os.path.join(BASE_DIR, "catalog.json"))


# ===================== Helpers =====================

def load_catalog():
    """Load catalog from JSON file"""
    try:
        with open(CATALOG_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        # Return empty catalog structure if file doesn't exist
        return {
            "schemaVersion": 1,
            "projectOwner": "Group17",
            "projectName": "SmartChill",
            "lastUpdate": datetime.now(timezone.utc).isoformat(),
            "broker": {"IP": "mosquitto", "port": "1883"},
            "deviceModels": {},
            "servicesList": [],
            "devicesList": [],
            "usersList": [
                {
                    "userID": "admin",
                    "userName": "Administrator",
                    "chatID": 123456789,
                    "devicesList": []
                }
            ]
        }

def save_catalog(catalog):
    """Save catalog to JSON file with updated timestamp"""
    catalog['lastUpdate'] = datetime.now(timezone.utc).isoformat()
    os.makedirs(os.path.dirname(CATALOG_FILE), exist_ok=True)
    try:
        with open(CATALOG_FILE, 'w') as f:
            json.dump(catalog, f, indent=4)
        print(f"[CATALOG] Catalog saved to {CATALOG_FILE}")
    except Exception as e:
        print(f"[ERROR] Failed to save catalog: {e}")
        raise

def generate_device_id(mac_address):
    """Generate device ID from MAC address"""
    return f"SmartChill_{mac_address.replace(':', '')[-6:]}"

def generate_device_topics(model, device_id, sensors):
    """Generate MQTT topics for a specific device"""
    topics = []
    for sensor in sensors:
        topic = f"Group17/SmartChill/Devices/{model}/{device_id}/{sensor}"
        topics.append(topic)
    
    # Add door_event topic
    door_event_topic = f"Group17/SmartChill/Devices/{model}/{device_id}/door_event"
    topics.append(door_event_topic)
    
    return topics