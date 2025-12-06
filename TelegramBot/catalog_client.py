import time
import re
import requests
from datetime import datetime, timezone

class CatalogError(Exception):
    """Custom exception for Catalog API errors."""
    def __init__(self, message, status_code=500):
        super().__init__(message)
        self.status_code = status_code

class CatalogClient:
    def __init__(self, catalog_url):
        self.catalog_url = catalog_url

    def request(self, method, path, json_data=None, timeout=6):
        """
        Generic HTTP request handler with error mapping.
        Replicates the original 'catalog_request' function logic.
        """
        url = f"{self.catalog_url}{path}"
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        
        try:
            r = requests.request(method, url, json=json_data, headers=headers, timeout=timeout)
            r.raise_for_status()
            return r.json() if r.content else {}
            
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code
            try:
                # Try to parse error message from JSON response
                detail_json = r.json()
                error_msg = detail_json.get('error', detail_json.get('detail', str(detail_json)))
            except Exception:
                # Fallback to text
                error_msg = r.text
            
            raise CatalogError(f"{method} {path} -> HTTP {status_code}: {error_msg}", status_code) from e
            
        except requests.RequestException as e:
            raise CatalogError(f"{method} {path} failed: {e}")

    # --- HTTP Method Wrappers ---
    def get(self, path): 
        return self.request("GET", path)

    def post(self, path, data): 
        return self.request("POST", path, json_data=data)

    def delete(self, path): 
        return self.request("DELETE", path)

    # --- Specific Logic ---

    def register_service(self, service_info, max_retries=5, base_delay=2.0):
        """
        Registers the bot service with the catalog.
        Includes the retry loop from the original 'register_with_catalog'.
        """
        payload = {
            "serviceID": service_info["serviceID"],
            "name": service_info["serviceName"],
            "description": service_info.get("serviceDescription", ""),
            "type": service_info.get("serviceType", "interface_bot"),
            "version": service_info.get("version", "1.0.0"),
            "endpoints": service_info.get("endpoints", []),
            "status": "active",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        for attempt in range(max_retries):
            try:
                r = requests.post(f"{self.catalog_url}/services/register", json=payload, timeout=5)
                if r.status_code in (200, 201):
                    print("[REGISTER] Registered with Catalog")
                    return True
                else:
                    print(f"[REGISTER] Failed (attempt {attempt+1}/{max_retries}): {r.status_code}")
            except requests.RequestException as e:
                print(f"[REGISTER] Error (attempt {attempt+1}/{max_retries}): {e}")
            
            if attempt < max_retries - 1:
                time.sleep(base_delay * (2 ** attempt))
        
        return False

    def find_device_by_mac(self, raw_mac):
        """
        Replicates '_find_device_by_mac'. 
        Fetches all devices and compares normalized MACs.
        """
        try:
            # Normalize input MAC
            target_mac = re.sub(r'[^0-9A-Fa-f]', '', raw_mac).upper()
            
            devices = self.get("/devices")
            for device in devices:
                # Normalize device MAC from catalog
                dev_mac_raw = device.get('mac_address', '')
                dev_mac = re.sub(r'[^0-9A-Fa-f]', '', dev_mac_raw).upper()
                
                if dev_mac == target_mac:
                    print(f"[CATALOG] Found device by MAC {raw_mac}: {device.get('deviceID')}")
                    return device
            
            print(f"[CATALOG] Device with MAC {raw_mac} not found.")
            return None
            
        except CatalogError as e:
            print(f"[CATALOG] Error searching devices by MAC: {e}")
            return None
        except Exception as e:
            print(f"[ERROR] Unexpected error in find_device_by_mac: {e}")
            return None

    def get_user_by_chat_id(self, chat_id):
        """
        Replicates '_is_registered'.
        Fetches all users and looks for a matching telegram_chat_id.
        """
        try:
            users = self.get("/users")
            for user in users:
                if str(user.get('telegram_chat_id')) == str(chat_id):
                    return user
            return None
        except CatalogError as e:
            print(f"[ERROR] Failed to check registration: {e}")
            return None