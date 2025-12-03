import requests
import time
import random
from modules.utils import normalize_mac

class CatalogError(Exception):
    pass

class CatalogClient:
    def __init__(self, settings):
        self.settings = settings
        self.catalog_url = settings["catalog"]["url"]
        self.service_info = settings["serviceInfo"]

    def _cat_get(self, endpoint):
        """Helper for GET requests to Catalog"""
        try:
            url = f"{self.catalog_url}{endpoint}"
            response = requests.get(url, timeout=5)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            raise CatalogError(f"Catalog GET failed: {e}")

    def _cat_post(self, endpoint, data):
        """Helper for POST requests to Catalog"""
        try:
            url = f"{self.catalog_url}{endpoint}"
            response = requests.post(url, json=data, timeout=5)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            raise CatalogError(f"Catalog POST failed: {e}")

    def _cat_delete(self, endpoint):
        """Helper for DELETE requests to Catalog"""
        try:
            url = f"{self.catalog_url}{endpoint}"
            response = requests.delete(url, timeout=5)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            raise CatalogError(f"Catalog DELETE failed: {e}")

    def register_service(self, max_retries=5, base_delay=2):
        """Register this service with the Catalog"""
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

    def check_registration(self):
        """Check if service is registered, if not re-register"""
        try:
            response = requests.get(f"{self.catalog_url}/services/{self.service_info['serviceID']}", timeout=5)
            if response.status_code != 200:
                print("[CHECK] Service not found in catalog, re-registering...")
                self.register_service()
        except requests.RequestException:
            pass

    def get_user(self, user_id):
        return self._cat_get(f"/users/{user_id}")

    def get_device(self, device_id):
        return self._cat_get(f"/devices/{device_id}")

    def get_user_devices(self, user_id):
        """Get all devices linked to a user"""
        try:
            user_info = self.get_user(user_id)
            device_ids = user_info.get('linked_devices', [])
            devices = []
            for dev_id in device_ids:
                try:
                    dev_info = self.get_device(dev_id)
                    devices.append(dev_info)
                except CatalogError:
                    continue
            return devices
        except CatalogError:
            return []

    def find_device_by_mac(self, mac_address):
        """Find a device by its MAC address (custom endpoint or search)"""
        # Assuming catalog has an endpoint or we search all devices
        # The original code searched all devices
        try:
            all_devices = self._cat_get("/devices")
            norm_mac = normalize_mac(mac_address)
            for device in all_devices:
                if normalize_mac(device.get('mac_address')) == norm_mac:
                    return device
            return None
        except CatalogError:
            return None

    def link_user_to_device(self, user_id, device_id):
        """Link a user to a device"""
        # This might involve updating user or device or both
        # Original code: POST /users/{user_id}/devices with {"device_id": device_id}
        # OR PUT /devices/{device_id} with {"owner": user_id}
        # Let's follow original logic if possible, or assume standard catalog API
        # Original code logic:
        # self._cat_post(f"/users/{user_id}/devices", {"device_id": device_id})
        return self._cat_post(f"/users/{user_id}/devices", {"device_id": device_id})

    def unlink_user_from_device(self, user_id, device_id):
        return self._cat_delete(f"/users/{user_id}/devices/{device_id}")

    def is_user_registered(self, user_id):
        try:
            self.get_user(user_id)
            return True
        except CatalogError:
            return False

    def register_user(self, user_data):
        return self._cat_post("/users", user_data)
