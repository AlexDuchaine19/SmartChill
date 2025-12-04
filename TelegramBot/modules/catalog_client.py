import requests
import time
import random
from datetime import datetime
from modules.utils import normalize_mac

class CatalogError(Exception):
    """Custom exception for catalog errors."""
    def __init__(self, message, status_code=500):
        super().__init__(message)
        self.status_code = status_code

class CatalogClient:
    def __init__(self, settings):
        self.settings = settings
        self.catalog_url = settings["catalog"]["url"]
        self.service_info = settings["serviceInfo"]

    def _request(self, method, endpoint, data=None, timeout=6):
        """Generic internal request handler with robust error parsing"""
        url = f"{self.catalog_url.rstrip('/')}/{endpoint.lstrip('/')}"
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        try:
            response = requests.request(method, url, json=data, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response.json() if response.content else {"status": "success", "code": response.status_code}
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code
            try:
                err_json = e.response.json()
                # Try to find the error message in common fields
                error_msg = err_json.get('error', err_json.get('detail', str(err_json)))
            except Exception:
                error_msg = e.response.text
            
            # Log for debugging but raise clean error
            print(f"[CATALOG_ERR] HTTP {status_code} for {method} {url}: {error_msg}")
            raise CatalogError(f"HTTP {status_code}: {error_msg}", status_code) from e
        except requests.RequestException as e:
            print(f"[CATALOG_ERR] Network error: {e}")
            raise CatalogError(f"Network error connecting to catalog: {e}")

    def _cat_get(self, endpoint): return self._request("GET", endpoint)
    def _cat_post(self, endpoint, data): return self._request("POST", endpoint, data)
    def _cat_delete(self, endpoint): return self._request("DELETE", endpoint)

    # --- Service Registration ---
    def register_service(self, max_retries=5, base_delay=2.0):
        """Register this service with the central catalog."""
        payload = {
            "serviceID": self.service_info["serviceID"],
            "name": self.service_info["serviceName"],
            "description": self.service_info.get("serviceDescription", ""),
            "type": self.service_info.get("serviceType", "microservice"),
            "version": self.service_info.get("version", "1.0.0"),
            "endpoints": self.service_info.get("endpoints", []),
            "status": "active",
            "timestamp": datetime.now().strftime("%d-%m-%Y %H:%M"),
        }
        
        for attempt in range(max_retries):
            try:
                self._request("POST", "/services/register", payload)
                print("[REGISTER] Registered with Catalog")
                return True
            except CatalogError as e:
                if e.status_code == 409: # Already registered is fine
                     print("[REGISTER] Service updated in Catalog.")
                     return True
                print(f"[REGISTER] Failed (attempt {attempt+1}/{max_retries}): {e}")
            
            if attempt < max_retries - 1:
                time.sleep(base_delay * (2 ** attempt))
        return False

    # --- User Logic ---

    def get_user(self, user_id):
        return self._cat_get(f"/users/{user_id}")

    def is_chat_id_linked(self, chat_id):
        """
        Checks if a Telegram chat_id is linked to ANY user. 
        Iterates through users because /users/by-chat endpoint might not exist.
        Returns userID if linked, None otherwise.
        """
        try:
            users = self._cat_get("/users")
            target = str(chat_id)
            for user in users:
                if str(user.get('telegram_chat_id')) == target:
                    linked_user = user.get('userID')
                    print(f"[AUTH] Chat ID {chat_id} is linked to '{linked_user}'.")
                    return linked_user
            print(f"[AUTH] Chat ID {chat_id} is not linked.")
            return None
        except CatalogError as e:
            print(f"[ERROR] Failed to check chat_id link: {e}")
            return None

    def register_user(self, user_data):
        """Register a new user. user_data = {userID, userName, telegram_chat_id}"""
        return self._cat_post("/users", user_data)

    def link_telegram(self, user_id, chat_id):
        """Link a chat_id to an existing user."""
        return self._cat_post(f"/users/{user_id}/link_telegram", {"chat_id": str(chat_id)})

    def delete_user(self, user_id):
        return self._cat_delete(f"/users/{user_id}")

    # --- Device Logic ---

    def get_device(self, device_id):
        return self._cat_get(f"/devices/{device_id}")

    def get_user_devices(self, user_id):
        return self._cat_get(f"/users/{user_id}/devices")

    def find_device_by_mac(self, mac_address):
        """
        Finds a device by MAC address. 
        Iterates through devices because /devices/by-mac endpoint might not exist.
        """
        try:
            normalized_target = normalize_mac(mac_address)
            if len(normalized_target) != 12:
                print(f"[VALIDATION] Invalid MAC length: {mac_address}")
                return None
                
            devices = self._cat_get("/devices")
            for device in devices:
                device_mac = normalize_mac(device.get('mac_address', ''))
                if device_mac == normalized_target:
                    print(f"[CATALOG] Found device by MAC {mac_address}: {device.get('deviceID')}")
                    return device
            
            print(f"[CATALOG] Device with MAC {mac_address} not found.")
            return None
        except CatalogError as e:
            print(f"[CATALOG] Error searching devices by MAC: {e}")
            return None

    def assign_device_to_user(self, user_id, device_id, device_name):
        return self._cat_post(f"/users/{user_id}/assign-device", {
            "device_id": device_id, 
            "device_name": device_name
        })

    def unassign_device(self, device_id):
        # Try standard unassign endpoint
        return self._cat_post(f"/devices/{device_id}/unassign", {})

    def rename_device(self, device_id, new_name):
        """Renames a device. Implements fallback (unassign+assign) if /rename endpoint fails."""
        try:
            # Try direct rename endpoint
            return self._cat_post(f"/devices/{device_id}/rename", {"user_device_name": new_name})
        except CatalogError as e:
            if e.status_code == 404:
                # Endpoint not found? Fallback logic.
                print(f"[RENAME] Direct rename failed ({e}), attempting fallback strategy...")
                
                # 1. Get current owner
                dev = self.get_device(device_id)
                user_id = dev.get('assigned_user')
                if not user_id:
                    raise CatalogError("Device not assigned, cannot rename via fallback.")
                
                # 2. Unassign
                self.unassign_device(device_id)
                
                # 3. Re-assign with new name
                return self.assign_device_to_user(user_id, device_id, new_name)
            
            # If it was another error (e.g. 500), re-raise
            raise e