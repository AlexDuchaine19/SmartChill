# Fridge Status Control Service (Modular)

This service monitors temperature and humidity data from SmartChill devices, detects abnormal conditions, sends alerts, and manages device configurations through a structured MQTT protocol.  
It is a modular rewrite of the original monolithic `StatusControl_Old.py`, preserving full functionality while improving clarity and maintainability.

---

## Overview

The service performs three main functions:

1. **Receive SenML sensor data** (temperature and humidity) via MQTT.  
2. **Detect out-of-range conditions** and publish alerts when needed.  
3. **Handle configuration requests** (get/update) from devices or administrators.

The system integrates with a **Catalog Service** (REST API) for device discovery and service registration.

---

## MQTT Topics

### Incoming Topics  
Configured in `settings.json` → `serviceInfo.endpoints`:

- `Group17/SmartChill/FridgeStatusControl/+/config_update` — configuration requests.
- `Group17/SmartChill/+/status` — SenML sensor data.

### Outgoing Topics

- Alerts  
  - `Group17/SmartChill/{deviceId}/Alerts/Temperature`  
  - `Group17/SmartChill/{deviceId}/Alerts/Humidity`

- Configuration responses  
  - `.../{requester}/config_data`  
  - `.../{requester}/config_ack`  
  - `.../{requester}/config_error`

---

## Expected Payloads

### SenML Example

```json
{
  "bn": "SmartChill_001/",
  "bt": 1700000000,
  "e": [
    { "n": "tempC", "v": 7.5, "t": 0 },
    { "n": "humidity", "v": 72, "t": 0 }
  ]
}
```

### Configuration Request Example

```json
{
  "type": "device_config_update",
  "device_id": "SmartChill_001",
  "config": {
    "min_temp_celsius": 2,
    "max_temp_celsius": 8,
    "enable_continuous_alerts": false
  }
}
```

---

## Modular Architecture

```
StatusControl.py        → Main orchestrator
modules/
 ├─ config_manager.py   → Full MQTT configuration protocol
 ├─ status_monitor.py   → Temperature/humidity evaluation + alerts
 ├─ mqtt_client.py      → MQTT connection and message forwarding
 ├─ catalog_client.py   → REST integration with Catalog Service
 └─ utils.py            → Settings load/save (versioned)
```

---

## Running the Service

```
python StatusControl.py
```

The service will:

- Register itself in the Catalog  
- Subscribe to required MQTT topics  
- Accept configuration commands  
- Process sensor readings  
- Publish alerts when needed  

---

## Summary

This modular version preserves the logic of the original service while providing a cleaner, maintainable architecture with reliable alerting and configuration management.
