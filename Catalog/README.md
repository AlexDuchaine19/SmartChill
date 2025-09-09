# SmartChill Catalog Service

The **SmartChill Catalog Service** is a RESTful API (built with CherryPy) that manages the IoT catalog for SmartChill devices, services, users, and MQTT topics.  
It reads/writes from `catalog.json` and provides discovery, registration, and monitoring features.

---

## Endpoints

### Health & Info
- **GET /health** → Returns service status and counts
```json
{
  "status": "healthy",
  "service": "SmartChill Catalog Service",
  "devices_count": 1,
  "services_count": 5,
  "timestamp": "2025-09-09T14:04:00Z"
}

### Health & Info
- **GET /info** → System information and statistics
```json
{
  "project": {
    "owner": "Group17",
    "name": "SmartChill",
    "last_update": "09-09-2025 14:04",
    "schema_version": 1
  },
  "broker": { "IP": "mosquitto", "port": 1883 },
  "statistics": {
    "total_devices": 1,
    "assigned_devices": 1,
    "unassigned_devices": 0,
    "total_users": 2,
    "total_services": 8,
    "supported_models": 3,
    "devices_by_model": { "Samsung_RF28T5001SR": 1 }
  },
  "supported_models": ["Samsung_RF28T5001SR","Whirlpool_WRF535SWHZ","LG_LRMVS3006S"]
}

### Devices
- **POST /devices/register** → Register or sync a device
```json
{
  "status": "registered",
  "device_id": "SmartChill_A4B2C3D91E7F",
  "model": "Samsung_RF28T5001SR",
  "mqtt_topics": [
    "Group17/SmartChill/Devices/Samsung_RF28T5001SR/SmartChill_A4B2C3D91E7F/temperature",
    "Group17/SmartChill/Devices/Samsung_RF28T5001SR/SmartChill_A4B2C3D91E7F/door_event"
  ],
  "broker": { "IP": "mosquitto", "port": 1883 },
  "message": "Device registered successfully"
}

- **GET /devices** → List all devices
```json
[
  {
    "deviceID": "SmartChill_A4B2C3D91E7F",
    "model": "Samsung_RF28T5001SR",
    "status": "registered",
    "user_assigned": true
  }
]

- **GET /devices/{device_id}** → Get device details
```json
{
  "deviceID": "SmartChill_A4B2C3D91E7F",
  "mac_address": "A4:B2:C3:D9:1E:7F",
  "model": "Samsung_RF28T5001SR",
  "firmware_version": "1.2.3",
  "sensors": ["temperature","humidity","gas","light"],
  "status": "registered",
  "user_assigned": true,
  "assigned_user": "alex",
  "user_device_name": "Frigo di Alex"
}
