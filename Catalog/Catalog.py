import cherrypy
import os
from modules.data_manager import CatalogDataManager
from modules.rest_api import CatalogAPI

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CATALOG_FILE = os.environ.get("CATALOG_FILE", os.path.join(BASE_DIR, "catalog.json"))

def get_dispatcher(api):
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
    d.connect('unassign_device', '/devices/:device_id/unassign', controller=api, action='unassign_device', conditions={'method': ['POST']})
    d.connect('rename_device', '/devices/:device_id/rename', controller=api, action='rename_device', conditions={'method': ['POST']})
    
    # Users
    d.connect('users', '/users', controller=api, action='get_users', conditions={'method': ['GET']})
    d.connect('create_user', '/users', controller=api, action='create_user', conditions={'method': ['POST']})
    d.connect('user', '/users/:user_id', controller=api, action='get_user', conditions={'method': ['GET']})
    d.connect('user_devices', '/users/:user_id/devices', controller=api, action='get_user_devices', conditions={'method': ['GET']})
    d.connect('assign_device', '/users/:user_id/assign-device', controller=api, action='assign_device_to_user', conditions={'method': ['POST']})
    d.connect('delete_user', '/users/:user_id', controller=api, action='delete_user', conditions={'method': ['DELETE']})

    d.connect('link_telegram', '/users/:user_id/link_telegram', controller=api, action='link_telegram', conditions={'method': ['POST']})
    d.connect('get_user_by_chat', '/users/by-chat/:chat_id', controller=api, action='get_user_by_chat', conditions={'method': ['GET']})

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

    print("=== SmartChill Catalog Service (Modular) ===")
    print("Starting on http://0.0.0.0:8001")
    print("Health check: http://localhost:8001/health")
    print("System info: http://localhost:8001/info")

    # Initialize modules
    data_manager = CatalogDataManager(CATALOG_FILE)
    api = CatalogAPI(data_manager)

    conf = {
        '/': {
            'request.dispatch': get_dispatcher(api),
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