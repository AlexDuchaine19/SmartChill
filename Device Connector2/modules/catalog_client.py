import requests

class CatalogClient:
    def __init__(self, settings):
        self.settings = settings
        self.catalog_url = settings["catalog_url"]

    def register_device(self, mac_address, model, firmware_version, sensors):
        """Register device with catalog service using new API"""
        print(f"[REG] Registering device {mac_address} with model {model}...")
        
        registration_data = {
            "mac_address": mac_address,
            "model": model,
            "firmware_version": firmware_version,
            "sensors": sensors
        }
        
        try:
            response = requests.post(
                f"{self.catalog_url}/devices/register",
                json=registration_data,
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            
            if response.status_code in [200, 201]:
                config = response.json()
                print(f"[REG] Registration successful: {config['status']}")
                print(f"[REG] Device ID: {config['device_id']}")
                print(f"[REG] MQTT Topics: {len(config.get('mqtt_topics', []))} topics assigned")
                return config
                
            else:
                print(f"[REG] Registration failed: {response.status_code}")
                error_msg = response.json() if response.content else "Unknown error"
                print(f"[REG] Error: {error_msg}")
                return None
                
        except requests.RequestException as e:
            print(f"[REG] Failed to connect to catalog: {e}")
            return None
