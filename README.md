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
