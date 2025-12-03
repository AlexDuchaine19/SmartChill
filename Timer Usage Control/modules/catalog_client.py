import requests
import time
import random

class CatalogClient:
    def __init__(self, settings):
        self.settings = settings
        self.catalog_url = settings["catalog"]["url"]
        self.service_info = settings["serviceInfo"]

    def register_service(self, max_retries=5, base_delay=2):
        """Register service with catalog via REST with retry logic"""
        for attempt in range(max_retries):
            try:
                registration_data = {
                    "serviceID": self.service_info["serviceID"],
                    "name": self.service_info["serviceName"],
                    "description": self.service_info["serviceDescription"],
                    "type": self.service_info["serviceType"],
                    "version": self.service_info["version"],
                    "endpoints": self.service_info["endpoints"],
                    "status": "active"
                }
                
                response = requests.post(
                    f"{self.catalog_url}/services/register",
                    json=registration_data,
                    timeout=5
                )
                
                if response.status_code in [200, 201]:
                    print(f"[REGISTER] Successfully registered with catalog")
                    return True
                else:
                    print(f"[REGISTER] Failed to register (attempt {attempt+1}/{max_retries}): {response.status_code}")
                    
            except requests.RequestException as e:
                print(f"[REGISTER] Error registering (attempt {attempt+1}/{max_retries}): {e}")
            
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                print(f"[REGISTER] Retrying in {delay:.1f} seconds...")
                time.sleep(delay)
        
        return False

    def check_device_exists(self, device_id):
        """Check if device exists in catalog via REST API"""
        try:
            response = requests.get(f"{self.catalog_url}/devices/{device_id}/exists", timeout=5)
            if response.status_code == 200:
                result = response.json()
                exists = result.get("exists", False)
                
                if not exists:
                    print(f"[DEVICE_CHECK] Device {device_id} not found in catalog")
                return exists
            else:
                print(f"[DEVICE_CHECK] Error checking device {device_id}: {response.status_code}")
                return False
                
        except requests.RequestException as e:
            print(f"[DEVICE_CHECK] Error connecting to catalog: {e}")
            return False

    def load_known_devices(self):
        """Load all registered devices from catalog at startup"""
        known_devices = set()
        try:
            response = requests.get(f"{self.catalog_url}/devices", timeout=5)
            if response.status_code == 200:
                devices = response.json()
                
                for device in devices:
                    device_id = device.get("deviceID")
                    if device_id and device_id.startswith("SmartChill_"):
                        known_devices.add(device_id)
                
                print(f"[INIT] Loaded {len(known_devices)} known devices from catalog")
                return known_devices
            else:
                print(f"[INIT] Failed to load devices from catalog: {response.status_code}")
                return set()
                
        except requests.RequestException as e:
            print(f"[INIT] Error loading devices from catalog: {e}")
            return set()
