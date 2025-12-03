import requests

class CatalogClient:
    def __init__(self, settings):
        self.settings = settings
        self.catalog_url = settings["catalog"]["url"]
        self.service_info = settings["serviceInfo"]

    def register_service(self):
        """Register service with catalog"""
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
            response = requests.post(f"{self.catalog_url}/services/register", json=registration_data, timeout=5)
            if response.status_code in [200, 201]:
                print("[REGISTER] Successfully registered with catalog")
                return True
            else:
                print(f"[REGISTER] Failed to register: {response.status_code}")
                return False
        except requests.RequestException as e:
            print(f"[REGISTER] Error: {e}")
            return False

    def get_devices(self):
        """Get all devices from catalog"""
        try:
            response = requests.get(f"{self.catalog_url}/devices", timeout=5)
            if response.status_code == 200:
                return response.json()
            return []
        except requests.RequestException:
            return []

    def get_models(self):
        """Get all device models from catalog"""
        try:
            response = requests.get(f"{self.catalog_url}/models", timeout=5)
            if response.status_code == 200:
                return response.json()
            return {}
        except requests.RequestException:
            return {}
