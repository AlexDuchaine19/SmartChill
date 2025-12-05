# InfluxDB Adaptor (Modular 1:1 Version)

This service is a fully modularized version of the original `InfluxDB_Adaptor_Old.py`, preserving *100% of its logic* while separating responsibilities across clean and reusable modules.

The behavior, processing flow, timestamp handling, MQTT routing, SenML parsing, door event interpretation, InfluxDB writing, Catalog integration, and REST API output are **identical** to the old service.

---

## Overview

The adaptor:

1. Receives SenML sensor data via MQTT.
2. Receives door state events via MQTT.
3. Parses and stores the data in InfluxDB 2.x using the same measurement/tag/field model.
4. Provides a REST API to query historical events and sensor values.
5. Registers itself in a Catalog Service and loads known devices.
6. Runs an optional status monitor thread that prints periodic diagnostics.

Nothing in the operational logic has been modified.

---

## Architecture

The service is divided into the following modules:

```
InfluxDB_Adaptor.py       → Main orchestrator (threads, startup, routing)
modules/
 ├─ influx_connection.py  → InfluxDB client, token loading, write API, point creation
 ├─ mqtt_manager.py       → MQTT setup, subscription, notify() forwarding
 ├─ senml_parser.py       → SenML decoding (bt + t), JSON extraction
 ├─ door_handler.py       → Door-event decoding + writing
 ├─ storage.py            → Unified interface for writing sensor & door data
 ├─ catalog_client.py     → Register service, check devices, load known devices
 ├─ status_monitor.py     → Heartbeat/status thread
 ├─ rest_api.py           → CherryPy REST API, identical endpoints
 └─ utils.py              → Settings loader/saver
```

Each module contains a docstring and uses the exact logic of the corresponding portion of `InfluxDB_Adaptor_Old.py`.

---

## MQTT Topics

Configured in `settings.json`:

- `Group17/SmartChill/+/SenML`  
- `Group17/SmartChill/+/Door`

Incoming messages are forwarded to:

- `senml_parser` for sensor readings  
- `door_handler` for door events  

No topic pattern or behavior has been altered.

---

## Data Stored in InfluxDB

Two measurements are written exactly as before:

### `sensor_data`
Tags:
- `device_id`
- `sensor`

Fields:
- `value`

### `door_events`
Tags:
- `device_id`

Fields:
- `event`

Timestamps use `WritePrecision.NS`, as in the old service.

---

## REST API Endpoints

All endpoints match the old adaptor exactly:

- `GET /events`  
- `GET /sensors/<type>`  
- `GET /latest/<type>`

Query parameters (duration, device, limit) behave the same way.

---

## How to Run

```
python InfluxDB_Adaptor.py
```

This launches:

- MQTT subscription  
- REST API server  
- Status monitor thread  
- InfluxDB writer  
- Catalog registration  
- Main loop  
All identical to the legacy service.

---

## Summary

This modular version preserves **100% functionality** of the old adaptor while offering:

- clear separation of components  
- maintainable structure  
- readable code  
- reusable modules

No functional changes, no rewritten logic, no altered behavior.
