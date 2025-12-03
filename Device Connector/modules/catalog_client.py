import requests
from modules.utils import save_settings

class CatalogClient:
    def __init__(self, settings):
        self.settings = settings
        self.catalog_url = settings["catalog_url"]
        self.mac_address = settings["deviceInfo"]["mac_address"]
        self.model = settings["deviceInfo"]["model"]
        self.firmware_version = settings["deviceInfo"]["firmware_version"]
        self.device_id = settings["deviceInfo"].get("deviceID")

    def register(self):
        """Register device with catalog service"""
        print(f"[REG] Registering device {self.mac_address} with model {self.model}...")
        
        registration_data = {
            "mac_address": self.mac_address,
            "model": self.model,
            "firmware_version": self.firmware_version,
            "sensors": self.settings["deviceInfo"]["sensors"]
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
                self.device_id = config["device_id"]
                
                print(f"[REG] Registration successful: {config['status']}")
                print(f"[REG] Device ID: {self.device_id}")
                print(f"[REG] MQTT Topics: {len(config.get('mqtt_topics', []))} topics assigned")
                
                # Update settings with new device ID
                self.settings["deviceInfo"]["deviceID"] = self.device_id
                save_settings(self.settings)
                return True, self.device_id
                
            else:
                print(f"[REG] Registration failed: {response.status_code}")
                error_msg = response.json() if response.content else "Unknown error"
                print(f"[REG] Error: {error_msg}")
                return False, None
                
        except requests.RequestException as e:
            print(f"[REG] Failed to connect to catalog: {e}")
            print("[REG] Proceeding with local configuration...")
            # Use MAC-based device_id as fallback
            if not self.device_id:
                self.device_id = f"SmartChill_{self.mac_address.replace(':', '')}"
            return False, self.device_id
