import cherrypy
import json
import os
from datetime import datetime


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
            "lastUpdate": datetime.now().strftime("%d-%m-%Y %H:%M"),
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
    catalog['lastUpdate'] = datetime.now().strftime("%d-%m-%Y %H:%M")
    os.makedirs(os.path.dirname(CATALOG_FILE), exist_ok=True)
    try:
        with open(CATALOG_FILE, 'w') as f:
            json.dump(catalog, f, indent=4)
        print(f"[CATALOG] Catalog saved to {CATALOG_FILE}")
    except Exception as e:
        print(f"[ERROR] Failed to save catalog: {e}")
        raise

def http_error(status_code, payload):
    """Set status and return JSON payload"""
    cherrypy.response.status = status_code
    return payload

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

# ===================== Controller =====================

class CatalogAPI:
    @cherrypy.tools.json_out()
    def health(self):
        """GET /health"""
        try:
            catalog = load_catalog()
            return {
                "status": "healthy",
                "service": "SmartChill Catalog Service",
                "timestamp": datetime.now().isoformat(),
                "devices_count": len(catalog.get('devicesList', [])),
                "services_count": len(catalog.get('servicesList', []))
            }
        except Exception as e:
            return http_error(500, {
                "status": "unhealthy",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            })

    @cherrypy.tools.json_out()
    def info(self):
        """GET /info - System information and statistics"""
        catalog = load_catalog()
        total_devices = len(catalog['devicesList'])
        assigned_devices = len([d for d in catalog['devicesList'] if d.get('user_assigned', False)])

        devices_by_model = {}
        for device in catalog['devicesList']:
            model = device['model']
            devices_by_model[model] = devices_by_model.get(model, 0) + 1

        stats = {
            "project": {
                "owner": catalog.get('projectOwner'),
                "name": catalog.get('projectName'),
                "last_update": catalog.get('lastUpdate'),
                "schema_version": catalog.get('schemaVersion', 1)
            },
            "broker": catalog.get('broker'),
            "statistics": {
                "total_devices": total_devices,
                "assigned_devices": assigned_devices,
                "unassigned_devices": total_devices - assigned_devices,
                "total_users": len(catalog['usersList']),
                "total_services": len(catalog['servicesList']),
                "supported_models": len(catalog.get('deviceModels', {})),
                "devices_by_model": devices_by_model
            },
            "supported_models": list(catalog.get('deviceModels', {}).keys())
        }
        return stats

    # ============= DEVICE REGISTRATION =============
    @cherrypy.tools.json_in()
    @cherrypy.tools.json_out()
    def register_device(self):
        """POST /devices/register - Register or sync device"""
        data = cherrypy.request.json or {}
        
        print(f"[DEVICE_REG] Received device registration: {json.dumps(data, indent=2)}")

        # Validate required fields
        required_fields = ['mac_address', 'model', 'sensors']
        for field in required_fields:
            if field not in data:
                error_msg = f"Missing required field: {field}"
                print(f"[DEVICE_REG] Error: {error_msg}")
                return http_error(400, {"error": error_msg})

        mac_address = data['mac_address']
        model = data['model']
        sensors = data['sensors']
        firmware_version = data.get('firmware_version', 'unknown')

        catalog = load_catalog()

        # Check if model is supported
        if model not in catalog.get('deviceModels', {}):
            return http_error(400, {
                "error": f"Model {model} not supported",
                "supported_models": list(catalog.get('deviceModels', {}).keys()),
                "received_model": model
            })

        device_id = generate_device_id(mac_address)

        # Check if device already exists (sync scenario)
        for i, device in enumerate(catalog['devicesList']):
            if device.get('mac_address') == mac_address:
                existing_device = device
                existing_device['last_sync'] = datetime.now().strftime("%d-%m-%Y %H:%M")
                catalog['devicesList'][i] = existing_device
                save_catalog(catalog)
                
                print(f"[DEVICE_REG] Device {device_id} synchronized")
                
                return {
                    "status": "synced",
                    "device_id": existing_device['deviceID'],
                    "model": existing_device['model'],
                    "mqtt_topics": existing_device['mqtt_topics'],
                    "broker": catalog['broker'],
                    "message": "Device configuration synchronized successfully"
                }

        # Register new device
        model_config = catalog['deviceModels'][model]
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
            "registration_time": datetime.now().strftime("%d-%m-%Y %H:%M"),
            "last_sync": datetime.now().strftime("%d-%m-%Y %H:%M")
        }

        catalog['devicesList'].append(new_device)
        save_catalog(catalog)
        
        print(f"[DEVICE_REG] New device {device_id} registered successfully")

        cherrypy.response.status = 201
        return {
            "status": "registered",
            "device_id": device_id,
            "model": model,
            "mqtt_topics": device_topics,
            "broker": catalog['broker'],
            "message": "Device registered successfully"
        }

    # ============= SERVICE REGISTRATION =============
    @cherrypy.tools.json_in()
    @cherrypy.tools.json_out()
    def register_service(self):
        """POST /services/register - Register service"""
        data = cherrypy.request.json or {}
        
        print(f"[SERVICE_REG] Received service registration")

        # Validate required fields
        required_fields = ['serviceID', 'name', 'description', 'endpoints']
        for field in required_fields:
            if field not in data:
                error_msg = f"Missing required field: {field}"
                print(f"[SERVICE_REG] Error: {error_msg}")
                return http_error(400, {"error": error_msg})

        service_id = data['serviceID']
        catalog = load_catalog()
        
        print(f"[SERVICE_REG] Processing service: {service_id}")

        # Check if service already exists
        for i, service in enumerate(catalog['servicesList']):
            if service.get('serviceID') == service_id:
                # Update existing service
                updated_service = {
                    "serviceID": service_id,
                    "name": data['name'],
                    "description": data['description'],
                    "endpoints": data['endpoints'],
                    "type": data.get('type', 'microservice'),
                    "version": data.get('version', '1.0.0'),
                    "status": "active",
                    "lastUpdate": datetime.now().strftime("%d-%m-%Y %H:%M")
                }
                catalog['servicesList'][i] = updated_service
                save_catalog(catalog)
                
                print(f"[SERVICE_REG] Service {service_id} updated successfully")
                
                return {
                    "status": "updated",
                    "service_id": service_id,
                    "message": "Service updated successfully"
                }

        # Register new service
        new_service = {
            "serviceID": service_id,
            "name": data['name'],
            "description": data['description'],
            "endpoints": data['endpoints'],
            "type": data.get('type', 'microservice'),
            "version": data.get('version', '1.0.0'),
            "status": "active",
            "registration_time": datetime.now().strftime("%d-%m-%Y %H:%M"),
            "lastUpdate": datetime.now().strftime("%d-%m-%Y %H:%M")
        }

        catalog['servicesList'].append(new_service)
        save_catalog(catalog)
        
        print(f"[SERVICE_REG] Service {service_id} registered successfully")

        cherrypy.response.status = 201
        return {
            "status": "registered",
            "service_id": service_id,
            "message": "Service registered successfully"
        }

    # ============= DEVICE MANAGEMENT =============
    @cherrypy.tools.json_out()
    def get_devices(self):
        """GET /devices"""
        catalog = load_catalog()
        return catalog['devicesList']

    @cherrypy.tools.json_out()
    def get_device(self, device_id):
        """GET /devices/{device_id}"""
        catalog = load_catalog()
        for device in catalog['devicesList']:
            if device['deviceID'] == device_id:
                return device
        return http_error(404, {"error": "Device not found"})

    @cherrypy.tools.json_out()
    def device_exists(self, device_id):
        """GET /devices/{device_id}/exists - Check if device exists"""
        catalog = load_catalog()
        exists = any(device['deviceID'] == device_id for device in catalog['devicesList'])
        return {
            "device_id": device_id,
            "exists": exists,
            "timestamp": datetime.now().isoformat()
        }

    @cherrypy.tools.json_out()
    def get_unassigned_devices(self):
        """GET /devices/unassigned"""
        catalog = load_catalog()
        unassigned = [d for d in catalog['devicesList'] if not d.get('user_assigned', False)]
        return unassigned

    @cherrypy.tools.json_out()
    def get_devices_by_model(self, model):
        """GET /devices/by-model/{model}"""
        catalog = load_catalog()
        model_devices = [d for d in catalog['devicesList'] if d.get('model') == model]
        return model_devices

    # ============= SERVICE DISCOVERY =============
    @cherrypy.tools.json_out()
    def get_services(self):
        """GET /services"""
        catalog = load_catalog()
        return catalog['servicesList']

    @cherrypy.tools.json_out()
    def get_service(self, service_id):
        """GET /services/{service_id}"""
        catalog = load_catalog()
        for service in catalog['servicesList']:
            if service['serviceID'] == service_id:
                return service
        return http_error(404, {"error": "Service not found"})

    # ============= USER MANAGEMENT =============
    @cherrypy.tools.json_out()
    def get_users(self):
        """GET /users"""
        catalog = load_catalog()
        return catalog['usersList']

    @cherrypy.tools.json_out()
    def get_user(self, user_id):
        """GET /users/{user_id}"""
        catalog = load_catalog()
        for user in catalog['usersList']:
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

        catalog = load_catalog()
        for user in catalog['usersList']:
            if user['userID'] == data['userID']:
                return http_error(409, {"error": "User already exists"})

        new_user = {
            "userID": data['userID'],
            "userName": data['userName'],
            "devicesList": [],
            "registration_time": datetime.now().strftime("%d-%m-%Y %H:%M")
        }
        catalog['usersList'].append(new_user)
        save_catalog(catalog)

        cherrypy.response.status = 201
        return {
            "message": f"User {data['userID']} created successfully",
            "user": new_user
        }
        
    @cherrypy.tools.json_out()
    def delete_user(self, user_id):
        """DELETE /users/{user_id}"""
        catalog = load_catalog()

        # Trovo e rimuovo l'utente
        user_to_delete = None
        for i, user in enumerate(catalog['usersList']):
            if user['userID'] == user_id:
                user_to_delete = catalog['usersList'].pop(i)
                break

        if not user_to_delete:
            return http_error(404, {"error": "User not found"})

        unassigned_devices = []
        for device in catalog.get('devicesList', []):
            if device.get('assigned_user') == user_id:
                device['user_assigned'] = False
                device['assigned_user'] = None
                device['user_device_name'] = None
                device['assignment_time'] = None
                unassigned_devices.append(device['deviceID'])

        save_catalog(catalog)

        return {
            "message": f"User {user_id} deleted successfully",
            "user": user_to_delete,
            "unassigned_devices": unassigned_devices
        }

    @cherrypy.tools.json_out()
    def get_user_devices(self, user_id):
        """GET /users/{user_id}/devices"""
        catalog = load_catalog()
        user = next((u for u in catalog['usersList'] if u['userID'] == user_id), None)
        if not user:
            return http_error(404, {"error": "User not found"})

        user_devices = []
        ids = [d['deviceID'] for d in user.get('devicesList', [])]
        for device in catalog['devicesList']:
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

        catalog = load_catalog()

        # Find user
        user = None
        user_index = None
        for i, u in enumerate(catalog['usersList']):
            if u['userID'] == user_id:
                user = u
                user_index = i
                break
        if not user:
            return http_error(404, {"error": "User not found"})

        # Find device
        device = None
        device_index = None
        for i, d in enumerate(catalog['devicesList']):
            if d['deviceID'] == device_id:
                device = d
                device_index = i
                break
        if not device:
            return http_error(404, {"error": "Device not found"})
        if device.get('user_assigned', False):
            return http_error(409, {"error": "Device already assigned to another user"})

        # Assign device to user
        user_device_entry = {"deviceID": device_id, "deviceName": device_name}
        user.setdefault('devicesList', []).append(user_device_entry)
        catalog['usersList'][user_index] = user

        # Mark device as assigned
        device['user_assigned'] = True
        device['assigned_user'] = user_id
        device['user_device_name'] = device_name
        device['assignment_time'] = datetime.now().strftime("%d-%m-%Y %H:%M")
        catalog['devicesList'][device_index] = device

        save_catalog(catalog)
        return {"message": f"Device {device_id} assigned to user {user_id}", "device": device}

    # ============= DEVICE MODELS =============
    @cherrypy.tools.json_out()
    def get_device_models(self):
        """GET /models"""
        catalog = load_catalog()
        return catalog.get('deviceModels', {})

    @cherrypy.tools.json_out()
    def get_device_model(self, model):
        """GET /models/{model}"""
        catalog = load_catalog()
        if model in catalog.get('deviceModels', {}):
            return catalog['deviceModels'][model]
        return http_error(404, {"error": "Device model not found"})

    # ============= MQTT TOPICS =============
    @cherrypy.tools.json_out()
    def get_mqtt_topics(self):
        """GET /mqtt/topics"""
        catalog = load_catalog()
        all_topics = {"device_topics": {}, "service_topics": {}}

        for device in catalog['devicesList']:
            device_id = device['deviceID']
            all_topics["device_topics"][device_id] = {
                "model": device['model'],
                "topics": device['mqtt_topics'],
                "mqtt_config": device.get('mqtt_config', {})
            }

        for service in catalog['servicesList']:
            service_id = service['serviceID']
            all_topics["service_topics"][service_id] = {
                "endpoints": service.get('endpoints', [])
            }

        return all_topics

    @cherrypy.tools.json_out()
    def get_device_mqtt_topics(self, device_id):
        """GET /mqtt/topics/{device_id}"""
        catalog = load_catalog()
        for device in catalog['devicesList']:
            if device['deviceID'] == device_id:
                return {
                    "device_id": device_id,
                    "model": device['model'],
                    "topics": device['mqtt_topics'],
                    "mqtt_config": device.get('mqtt_config', {})
                }
        return http_error(404, {"error": "Device not found"})

# ===================== App Setup =====================

def get_dispatcher():
    api = CatalogAPI()
    d = cherrypy.dispatch.RoutesDispatcher()

    # Health & Info
    d.connect('health', '/health', controller=api, action='health', conditions={'method': ['GET']})
    d.connect('info', '/info', controller=api, action='info', conditions={'method': ['GET']})

    # Device registration
    d.connect('register_device', '/devices/register', controller=api, action='register_device', conditions={'method': ['POST']})
    
    # Service registration
    d.connect('register_service', '/services/register', controller=api, action='register_service', conditions={'method': ['POST']})

    # Devices
    d.connect('devices', '/devices', controller=api, action='get_devices', conditions={'method': ['GET']})
    d.connect('device', '/devices/:device_id', controller=api, action='get_device', conditions={'method': ['GET']})
    d.connect('device_exists', '/devices/:device_id/exists', controller=api, action='device_exists', conditions={'method': ['GET']})
    d.connect('unassigned', '/devices/unassigned', controller=api, action='get_unassigned_devices', conditions={'method': ['GET']})
    d.connect('devices_by_model', '/devices/by-model/:model', controller=api, action='get_devices_by_model', conditions={'method': ['GET']})

    # Users
    d.connect('users', '/users', controller=api, action='get_users', conditions={'method': ['GET']})
    d.connect('create_user', '/users', controller=api, action='create_user', conditions={'method': ['POST']})
    d.connect('user', '/users/:user_id', controller=api, action='get_user', conditions={'method': ['GET']})
    d.connect('user_devices', '/users/:user_id/devices', controller=api, action='get_user_devices', conditions={'method': ['GET']})
    d.connect('assign_device', '/users/:user_id/assign-device', controller=api, action='assign_device_to_user', conditions={'method': ['POST']})
    d.connect('delete_user', '/users/:user_id', controller=api, action='delete_user', conditions={'method': ['DELETE']})

    # Services
    d.connect('services', '/services', controller=api, action='get_services', conditions={'method': ['GET']})
    d.connect('service', '/services/:service_id', controller=api, action='get_service', conditions={'method': ['GET']})

    # MQTT
    d.connect('mqtt_topics', '/mqtt/topics', controller=api, action='get_mqtt_topics', conditions={'method': ['GET']})
    d.connect('mqtt_device_topics', '/mqtt/topics/:device_id', controller=api, action='get_device_mqtt_topics', conditions={'method': ['GET']})

    # Models
    d.connect('models', '/models', controller=api, action='get_device_models', conditions={'method': ['GET']})
    d.connect('model', '/models/:model', controller=api, action='get_device_model', conditions={'method': ['GET']})

    return d

def run_server():
    os.makedirs(os.path.dirname(CATALOG_FILE), exist_ok=True)

    print("=== SmartChill Catalog Service (CherryPy) ===")
    print("Starting on http://0.0.0.0:8001")
    print("Health check: http://localhost:8001/health")
    print("System info: http://localhost:8001/info")

    conf = {
        '/': {
            'request.dispatch': get_dispatcher(),
            'tools.response_headers.on': True,
            'tools.response_headers.headers': [('Content-Type', 'application/json; charset=utf-8')],
        }
    }

    cherrypy.config.update({
        'server.socket_host': '0.0.0.0',
        'server.socket_port': 8001,
        'engine.autoreload.on': True,
        'log.screen': True
    })

    cherrypy.quickstart(root=None, config=conf)

if __name__ == '__main__':
    run_server()