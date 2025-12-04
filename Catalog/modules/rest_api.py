import cherrypy
import json
from datetime import datetime, timezone
from modules.utils import http_error

class CatalogAPI:
    def __init__(self, data_manager):
        self.data_manager = data_manager

    @cherrypy.tools.json_out()
    def health(self):
        """GET /health"""
        try:
            catalog = self.data_manager.load_catalog()
            return {
                "status": "healthy",
                "service": "SmartChill Catalog Service",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "devices_count": len(catalog.get('devicesList', [])),
                "services_count": len(catalog.get('servicesList', []))
            }
        except Exception as e:
            return http_error(500, {
                "status": "unhealthy",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            })

    @cherrypy.tools.json_out()
    def info(self):
        """GET /info"""
        return self.data_manager.get_stats()

    # ============= DEVICE REGISTRATION =============
    @cherrypy.tools.json_in()
    @cherrypy.tools.json_out()
    def register_device(self):
        """POST /devices/register"""
        data = cherrypy.request.json or {}
        print(f"[DEVICE_REG] Received device registration: {json.dumps(data, indent=2)}")

        required_fields = ['mac_address', 'model', 'sensors']
        for field in required_fields:
            if field not in data:
                return http_error(400, {"error": f"Missing required field: {field}"})

        device, status = self.data_manager.register_device(
            data['mac_address'], 
            data['model'], 
            data['sensors'], 
            data.get('firmware_version', 'unknown')
        )

        if not device:
            return http_error(400, {"error": status})

        if status == "synced":
            print(f"[DEVICE_REG] Device {device['deviceID']} synchronized")
            return {
                "status": "synced",
                "device_id": device['deviceID'],
                "model": device['model'],
                "mqtt_topics": device['mqtt_topics'],
                "broker": self.data_manager.catalog['broker'],
                "message": "Device configuration synchronized successfully"
            }
        else:
            print(f"[DEVICE_REG] New device {device['deviceID']} registered successfully")
            cherrypy.response.status = 201
            return {
                "status": "registered",
                "device_id": device['deviceID'],
                "model": device['model'],
                "mqtt_topics": device['mqtt_topics'],
                "broker": self.data_manager.catalog['broker'],
                "message": "Device registered successfully"
            }

    # ============= SERVICE REGISTRATION =============
    @cherrypy.tools.json_in()
    @cherrypy.tools.json_out()
    def register_service(self):
        """POST /services/register"""
        data = cherrypy.request.json or {}
        print(f"[SERVICE_REG] Received service registration")

        required_fields = ['serviceID', 'name', 'description', 'endpoints']
        for field in required_fields:
            if field not in data:
                return http_error(400, {"error": f"Missing required field: {field}"})

        service, status = self.data_manager.register_service(data)
        
        print(f"[SERVICE_REG] Service {service['serviceID']} {status} successfully")
        
        if status == "registered":
            cherrypy.response.status = 201

        return {
            "status": status,
            "service_id": service['serviceID'],
            "message": f"Service {status} successfully"
        }

    # ============= DEVICE MANAGEMENT =============
    @cherrypy.tools.json_out()
    def get_devices(self):
        """GET /devices"""
        return self.data_manager.catalog['devicesList']
    
    @cherrypy.expose
    @cherrypy.tools.json_in()
    @cherrypy.tools.json_out()
    def rename_device(self, device_id):
        """POST /devices/{device_id}/rename"""
        data = cherrypy.request.json or {}
        new_name = data.get("user_device_name", "").strip()
        
        if not new_name:
            return http_error(400, {"error": "user_device_name is required"})
        
        if len(new_name) > 50:
            return http_error(400, {"error": "Device name too long (max 50 characters)"})
        
        device = self.data_manager.rename_device(device_id, new_name)
        if not device:
            return http_error(404, {"error": "Device not found"})
        
        return {
            "message": f"Device {device_id} renamed to '{new_name}'",
            "device": device
        }

    @cherrypy.tools.json_out()
    def get_device(self, device_id):
        """GET /devices/{device_id}"""
        for device in self.data_manager.catalog['devicesList']:
            if device['deviceID'] == device_id:
                return device
        return http_error(404, {"error": "Device not found"})

    @cherrypy.tools.json_out()
    def device_exists(self, device_id):
        """GET /devices/{device_id}/exists"""
        exists = any(device['deviceID'] == device_id for device in self.data_manager.catalog['devicesList'])
        return {
            "device_id": device_id,
            "exists": exists,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    @cherrypy.tools.json_out()
    def get_unassigned_devices(self):
        """GET /devices/unassigned"""
        return [d for d in self.data_manager.catalog['devicesList'] if not d.get('user_assigned', False)]

    @cherrypy.tools.json_out()
    def get_devices_by_model(self, model):
        """GET /devices/by-model/{model}"""
        return [d for d in self.data_manager.catalog['devicesList'] if d.get('model') == model]

    @cherrypy.tools.json_out()
    def unassign_device(self, device_id: str):
        """POST /devices/{device_id}/unassign"""
        device, status = self.data_manager.unassign_device(device_id)
        
        if status == "not_found":
            return http_error(404, {"error": "Device not found"})
        
        if status == "already_unassigned":
            return {
                "message": f"Device {device_id} already unassigned",
                "device_id": device_id,
                "already_unassigned": True
            }

        return {
            "message": f"Device {device_id} unassigned successfully",
            "device_id": device_id,
            "already_unassigned": False
        }

    # ============= SERVICE DISCOVERY =============
    @cherrypy.tools.json_out()
    def get_services(self):
        """GET /services"""
        return self.data_manager.catalog['servicesList']

    @cherrypy.tools.json_out()
    def get_service(self, service_id):
        """GET /services/{service_id}"""
        for service in self.data_manager.catalog['servicesList']:
            if service['serviceID'] == service_id:
                return service
        return http_error(404, {"error": "Service not found"})

    # ============= USER MANAGEMENT =============
    @cherrypy.tools.json_out()
    def get_users(self):
        """GET /users"""
        return self.data_manager.catalog['usersList']

    @cherrypy.tools.json_out()
    def get_user(self, user_id):
        """GET /users/{user_id}"""
        for user in self.data_manager.catalog['usersList']:
            if user['userID'] == user_id:
                return user
        return http_error(404, {"error": "User not found"})

    @cherrypy.tools.json_in()
    @cherrypy.tools.json_out()
    def create_user(self):
        """POST /users"""
        data = cherrypy.request.json or {}
        for field in ['userID', 'userName']:
            if field not in data:
                return http_error(400, {"error": f"Missing required field: {field}"})

        user, status = self.data_manager.create_user(data)
        if status == "exists":
            return http_error(409, {"error": "User already exists"})

        cherrypy.response.status = 201
        return {
            "message": f"User {data['userID']} created successfully",
            "user": user
        }

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def delete_user(self, user_id):
        """DELETE /users/{user_id}"""
        user, unassigned_devices = self.data_manager.delete_user(user_id)
        
        if not user:
            cherrypy.response.status = 404
            return {"error": f"User '{user_id}' not found"}

        return {
            "message": f"User '{user_id}' deleted successfully",
            "user": user,
            "unassigned_devices": unassigned_devices,
            "unassigned_count": len(unassigned_devices)
        }

    @cherrypy.tools.json_out()
    def get_user_devices(self, user_id):
        """GET /users/{user_id}/devices"""
        user = next((u for u in self.data_manager.catalog['usersList'] if u['userID'] == user_id), None)
        if not user:
            return http_error(404, {"error": "User not found"})

        user_devices = []
        ids = [d['deviceID'] for d in user.get('devicesList', [])]
        for device in self.data_manager.catalog['devicesList']:
            if device['deviceID'] in ids:
                user_devices.append(device)
        return user_devices

    @cherrypy.tools.json_in()
    @cherrypy.tools.json_out()
    def assign_device_to_user(self, user_id):
        """POST /users/{user_id}/assign-device"""
        data = cherrypy.request.json or {}
        device_id = data.get('device_id')
        device_name = data.get('device_name', 'My Fridge')

        if not device_id:
            return http_error(400, {"error": "device_id is required"})

        device, status = self.data_manager.assign_device_to_user(user_id, device_id, device_name)
        
        if status == "user_not_found":
            return http_error(404, {"error": "User not found"})
        if status == "device_not_found":
            return http_error(404, {"error": "Device not found"})
        if status == "device_assigned":
            return http_error(409, {"error": "Device already assigned to another user"})

        return {"message": f"Device {device_id} assigned to user {user_id}", "device": device}
    
    @cherrypy.expose
    @cherrypy.tools.json_in()
    @cherrypy.tools.json_out()
    def link_telegram(self, user_id):
        """POST /users/{user_id}/link_telegram"""
        data = cherrypy.request.json or {}
        chat_id = str(data.get("chat_id", "")).strip()
        if not chat_id:
            return http_error(400, {"error": "Missing chat_id"})

        success = self.data_manager.link_telegram(user_id, chat_id)
        if success:
            return {"message": f"Linked Telegram chat {chat_id} to user {user_id}"}
        
        return http_error(404, {"error": "User not found"})

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def get_user_by_chat(self, chat_id):
        """GET /users/by-chat/{chat_id}"""
        user = self.data_manager.get_user_by_chat_id(chat_id)
        if user:
            return user
        return http_error(404, {"error": "User not found"})

    # ============= DEVICE MODELS =============
    @cherrypy.tools.json_out()
    def get_device_models(self):
        """GET /models"""
        return self.data_manager.catalog.get('deviceModels', {})

    @cherrypy.tools.json_out()
    def get_device_model(self, model):
        """GET /models/{model}"""
        models = self.data_manager.catalog.get('deviceModels', {})
        if model in models:
            return models[model]
        return http_error(404, {"error": "Device model not found"})

    # ============= MQTT TOPICS =============
    @cherrypy.tools.json_out()
    def get_mqtt_topics(self):
        """GET /mqtt/topics"""
        all_topics = {"device_topics": {}, "service_topics": {}}

        for device in self.data_manager.catalog['devicesList']:
            device_id = device['deviceID']
            all_topics["device_topics"][device_id] = {
                "model": device['model'],
                "topics": device['mqtt_topics'],
                "mqtt_config": device.get('mqtt_config', {})
            }

        for service in self.data_manager.catalog['servicesList']:
            service_id = service['serviceID']
            all_topics["service_topics"][service_id] = {
                "endpoints": service.get('endpoints', [])
            }

        return all_topics

    @cherrypy.tools.json_out()
    def get_device_mqtt_topics(self, device_id):
        """GET /mqtt/topics/{device_id}"""
        for device in self.data_manager.catalog['devicesList']:
            if device['deviceID'] == device_id:
                return {
                    "device_id": device_id,
                    "model": device['model'],
                    "topics": device['mqtt_topics'],
                    "mqtt_config": device.get('mqtt_config', {})
                }
        return http_error(404, {"error": "Device not found"})
