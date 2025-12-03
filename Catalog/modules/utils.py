import cherrypy

def http_error(status_code, payload):
    """Set status and return JSON payload"""
    cherrypy.response.status = status_code
    return payload

def generate_device_id(mac_address):
    """Generate device ID from MAC address"""
    return f"SmartChill_{mac_address.replace(':', '').replace('-', '').upper()[-6:]}"

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
