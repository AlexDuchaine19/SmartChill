import json
import os
from datetime import datetime, timezone
from modules.utils import generate_device_topics, generate_device_id

class CatalogDataManager:
    def __init__(self, catalog_file):
        self.catalog_file = catalog_file
        self.catalog = self.load_catalog()

    def load_catalog(self):
        """Load catalog from JSON file"""
        try:
            with open(self.catalog_file, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
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

    def save_catalog(self):
        """Save catalog to JSON file with updated timestamp"""
        self.catalog['lastUpdate'] = datetime.now(timezone.utc).isoformat()
        os.makedirs(os.path.dirname(self.catalog_file), exist_ok=True)
        try:
            with open(self.catalog_file, 'w') as f:
                json.dump(self.catalog, f, indent=4)
            print(f"[CATALOG] Catalog saved to {self.catalog_file}")
        except Exception as e:
            print(f"[ERROR] Failed to save catalog: {e}")
            raise

    def get_stats(self):
        total_devices = len(self.catalog['devicesList'])
        assigned_devices = len([d for d in self.catalog['devicesList'] if d.get('user_assigned', False)])

        devices_by_model = {}
        for device in self.catalog['devicesList']:
            model = device['model']
            devices_by_model[model] = devices_by_model.get(model, 0) + 1

        return {
            "project": {
                "owner": self.catalog.get('projectOwner'),
                "name": self.catalog.get('projectName'),
                "last_update": self.catalog.get('lastUpdate'),
                "schema_version": self.catalog.get('schemaVersion', 1)
            },
            "broker": self.catalog.get('broker'),
            "statistics": {
                "total_devices": total_devices,
                "assigned_devices": assigned_devices,
                "unassigned_devices": total_devices - assigned_devices,
                "total_users": len(self.catalog['usersList']),
                "total_services": len(self.catalog['servicesList']),
                "supported_models": len(self.catalog.get('deviceModels', {})),
                "devices_by_model": devices_by_model
            },
            "supported_models": list(self.catalog.get('deviceModels', {}).keys())
        }

    def register_device(self, mac_address, model, sensors, firmware_version):
        if model not in self.catalog.get('deviceModels', {}):
            return None, f"Model {model} not supported"

        clean_mac = mac_address.replace(":", "").replace("-", "").upper()
        device_id = f"SmartChill_{clean_mac}"

        # Sync existing
        for i, device in enumerate(self.catalog['devicesList']):
            if device.get('mac_address') == mac_address:
                existing_device = device
                existing_device['last_sync'] = datetime.now(timezone.utc).isoformat()
                self.catalog['devicesList'][i] = existing_device
                self.save_catalog()
                return existing_device, "synced"

        # Register new
        model_config = self.catalog['deviceModels'][model]
        device_topics = generate_device_topics(model, device_id, sensors)

        new_device = {
            "deviceID": device_id,
            "mac_address": mac_address,
            "model": model,
            "firmware_version": firmware_version,
            "sensors": sensors,
            "mqtt_topics": device_topics,
            "mqtt_config": model_config.get('mqtt', {}),
            "status": "registered",
            "user_assigned": False,
            "owner": False,
            "registration_time": datetime.now(timezone.utc).isoformat(),
            "last_sync": datetime.now(timezone.utc).isoformat()
        }

        self.catalog['devicesList'].append(new_device)
        self.save_catalog()
        return new_device, "registered"

    def register_service(self, service_data):
        service_id = service_data['serviceID']
        
        for i, service in enumerate(self.catalog['servicesList']):
            if service.get('serviceID') == service_id:
                updated_service = {
                    "serviceID": service_id,
                    "name": service_data['name'],
                    "description": service_data['description'],
                    "endpoints": service_data['endpoints'],
                    "type": service_data.get('type', 'microservice'),
                    "version": service_data.get('version', '1.0.0'),
                    "status": "active",
                    "lastUpdate": datetime.now(timezone.utc).isoformat()
                }
                self.catalog['servicesList'][i] = updated_service
                self.save_catalog()
                return updated_service, "updated"

        new_service = {
            "serviceID": service_id,
            "name": service_data['name'],
            "description": service_data['description'],
            "endpoints": service_data['endpoints'],
            "type": service_data.get('type', 'microservice'),
            "version": service_data.get('version', '1.0.0'),
            "status": "active",
            "registration_time": datetime.now(timezone.utc).isoformat(),
            "lastUpdate": datetime.now(timezone.utc).isoformat()
        }

        self.catalog['servicesList'].append(new_service)
        self.save_catalog()
        return new_service, "registered"

    def rename_device(self, device_id, new_name):
        device_found = False
        device_data = None
        
        for device in self.catalog['devicesList']:
            if device['deviceID'] == device_id:
                device['user_device_name'] = new_name
                device_data = device
                device_found = True
                break
        
        if not device_found:
            return None

        if device_data.get('user_assigned') and device_data.get('owner'):
            assigned_user_id = device_data['owner']
            for user in self.catalog['usersList']:
                if user['userID'] == assigned_user_id:
                    for user_device in user.get('devicesList', []):
                        if user_device['deviceID'] == device_id:
                            user_device['deviceName'] = new_name
                            break
                    break
        
        self.save_catalog()
        return device_data

    def unassign_device(self, device_id):
        device_to_unassign = next(
            (d for d in self.catalog.get('devicesList', []) if d.get('deviceID') == device_id),
            None
        )
        if not device_to_unassign:
            return None, "not_found"

        if not device_to_unassign.get('user_assigned'):
            return device_to_unassign, "already_unassigned"

        assigned_user_id = device_to_unassign.get('owner')
        user_to_update = next(
            (u for u in self.catalog.get('usersList', []) if u.get('userID') == assigned_user_id),
            None
        )
        
        if user_to_update:
            devices_list = user_to_update.get('devicesList', [])
            for i, dev in enumerate(devices_list):
                if dev.get('deviceID') == device_id:
                    devices_list.pop(i)
                    break

        device_to_unassign['user_assigned'] = False
        device_to_unassign['owner'] = None
        device_to_unassign['user_device_name'] = None
        device_to_unassign['assignment_time'] = None

        self.save_catalog()
        return device_to_unassign, "success"

    def create_user(self, user_data):
        for user in self.catalog['usersList']:
            if user['userID'].lower() == user_data['userID'].lower():
                return None, "exists"

        new_user = {
            "userID": user_data['userID'].lower(),
            "userName": user_data['userName'],
            "telegram_chat_id": user_data.get("telegram_chat_id", None),
            "devicesList": [],
            "registration_time": datetime.now(timezone.utc).isoformat()
        }

        self.catalog['usersList'].append(new_user)
        self.save_catalog()
        return new_user, "created"

    def delete_user(self, user_id):
        user_id_str = str(user_id)
        user_to_delete = None
        
        for i, user in enumerate(self.catalog.get('usersList', [])):
            if str(user.get('userID')) == user_id_str:
                user_to_delete = self.catalog['usersList'].pop(i)
                break
        
        if not user_to_delete:
            return None, []

        unassigned_devices = []
        for device in self.catalog.get('devicesList', []):
            if str(device.get('owner')) == user_id_str:
                device['user_assigned'] = False
                device['owner'] = None
                device['user_device_name'] = None
                device['assignment_time'] = None
                unassigned_devices.append(device['deviceID'])

        self.save_catalog()
        return user_to_delete, unassigned_devices

    def assign_device_to_user(self, user_id, device_id, device_name):
        user = next((u for u in self.catalog['usersList'] if u['userID'] == user_id), None)
        if not user:
            return None, "user_not_found"

        device = next((d for d in self.catalog['devicesList'] if d['deviceID'] == device_id), None)
        if not device:
            return None, "device_not_found"
        
        if device.get('user_assigned', False):
            return None, "device_assigned"

        user_device_entry = {"deviceID": device_id, "deviceName": device_name}
        user.setdefault('devicesList', []).append(user_device_entry)

        device['user_assigned'] = True
        device['owner'] = user_id
        device['user_device_name'] = device_name
        device['assignment_time'] = datetime.now(timezone.utc).isoformat()

        self.save_catalog()
        return device, "success"

    def link_telegram(self, user_id, chat_id):
        for user in self.catalog['usersList']:
            if user['userID'].lower() == user_id.lower():
                user['telegram_chat_id'] = chat_id
                self.save_catalog()
                return True
        return False
