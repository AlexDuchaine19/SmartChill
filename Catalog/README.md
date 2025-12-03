# SmartChill Catalog Service

The **SmartChill Catalog Service** is a RESTful API (CherryPy) that manages the IoT catalog for SmartChill devices, services, users, and MQTT topics.  
It reads/writes from `catalog.json` and provides discovery, registration, and monitoring features.

---

## Endpoints & Example Responses

### Health & Info
**GET /health**
```json
{
  "status": "healthy",
  "service": "SmartChill Catalog Service",
  "devices_count": 1,
  "services_count": 5,
  "timestamp": "2025-09-09T14:04:00Z"
}
```

**GET /info**
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
```

---

### Devices
**POST /devices/register**
```json
{
  "status": "registered",
  "device_id": "SmartChill_A4B2C3D91E7F",
  "model": "Samsung_RF28T5001SR",
  "mqtt_topics": [".../temperature",".../door_event"],
  "broker": { "IP": "mosquitto", "port": 1883 },
  "message": "Device registered successfully"
}
```

**GET /devices**
```json
[
  {
    "deviceID": "SmartChill_A4B2C3D91E7F",
    "model": "Samsung_RF28T5001SR",
    "status": "registered",
    "user_assigned": true
  }
]
```

**GET /devices/{device_id}**
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
```

**GET /devices/{device_id}/exists**
```json
{
  "device_id": "SmartChill_A4B2C3D91E7F",
  "exists": true,
  "timestamp": "2025-09-09T14:05:00Z"
}
```

**GET /devices/unassigned**
```json
[]
```

**GET /devices/by-model/{model}**
```json
[
  {
    "deviceID": "SmartChill_A4B2C3D91E7F",
    "model": "Samsung_RF28T5001SR"
  }
]
```

---

### Services
**POST /services/register**
```json
{
  "status": "registered",
  "service_id": "TimerUsageControl",
  "message": "Service registered successfully"
}
```

**GET /services**
```json
[
  {
    "serviceID": "TimerUsageControl",
    "name": "Timer Usage Control Service",
    "type": "monitoring_service",
    "status": "active"
  }
]
```

**GET /services/{service_id}**
```json
{
  "serviceID": "InfluxDBAdaptor",
  "name": "InfluxDB Data Storage Adaptor",
  "description": "Stores sensor data in InfluxDB",
  "endpoints": ["MQTT Subscribe: ...", "REST: GET /health"],
  "type": "database_adaptor",
  "version": "1.0.0",
  "status": "active"
}
```

---

### Users
**POST /users**
```json
{
  "message": "User alex created successfully",
  "user": {
    "userID": "alex",
    "userName": "Alex",
    "chatID": null,
    "devicesList": []
  }
}
```

**GET /users**
```json
[
  { "userID": "admin", "userName": "Administrator" },
  { "userID": "alex", "userName": "Alex" }
]
```

**GET /users/{user_id}**
```json
{
  "userID": "alex",
  "userName": "Alex",
  "devicesList": [
    { "deviceID": "SmartChill_A4B2C3D91E7F", "deviceName": "Frigo di Alex" }
  ]
}
```

**GET /users/{user_id}/devices**
```json
[
  {
    "deviceID": "SmartChill_A4B2C3D91E7F",
    "model": "Samsung_RF28T5001SR",
    "user_device_name": "Frigo di Alex"
  }
]
```

**POST /users/{user_id}/assign-device**
```json
{
  "message": "Device SmartChill_A4B2C3D91E7F assigned to user alex",
  "device": {
    "deviceID": "SmartChill_A4B2C3D91E7F",
    "assigned_user": "alex",
    "user_device_name": "Frigo di Alex"
  }
}
```

---

### Models
**GET /models**
```json
{
  "Samsung_RF28T5001SR": { "brand": "Samsung", "type": "Smart Refrigerator Premium" },
  "Whirlpool_WRF535SWHZ": { "brand": "Whirlpool", "type": "Standard Refrigerator" }
}
```

**GET /models/{model}**
```json
{
  "brand": "Samsung",
  "type": "Smart Refrigerator Premium",
  "capacity_liters": 614,
  "energy_class": "A+++",
  "sensors": ["temperature","humidity","gas","light"]
}
```

---

### MQTT
**GET /mqtt/topics**
```json
{
  "device_topics": {
    "SmartChill_A4B2C3D91E7F": {
      "model": "Samsung_RF28T5001SR",
      "topics": [".../temperature",".../door_event"]
    }
  },
  "service_topics": {
    "TimerUsageControl": {
      "endpoints": ["MQTT Subscribe: ...", "MQTT Publish: ..."]
    }
  }
}
```

**GET /mqtt/topics/{device_id}**
```json
{
  "device_id": "SmartChill_A4B2C3D91E7F",
  "model": "Samsung_RF28T5001SR",
  "topics": [".../temperature",".../door_event"]
}
```

---

## Configuration
- **Broker:** Defined in `catalog.json` (`mosquitto:1883`)  
- **Data file:** Default `catalog.json` (override with `CATALOG_FILE` env var)  

---

## Run
```bash
python Catalog.py
# Service runs at http://localhost:8001
```

## Modular Architecture

This service has been refactored into a modular architecture to improve maintainability and scalability.

- **`modules/utils.py`**: Helper functions for settings management and common utilities.
- **`modules/data_manager.py`**: Handles core catalog operations (device/service/user management).
- **`modules/rest_api.py`**: Contains the REST API handlers for CherryPy.
- **`Catalog.py`**: The entry point that initializes and orchestrates the modules.
