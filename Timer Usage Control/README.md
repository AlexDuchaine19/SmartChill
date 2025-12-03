# SmartChill Timer Usage Control Service

The **Timer Usage Control Service** monitors fridge door activity via MQTT and triggers alerts when doors stay open too long.  
It also sends a confirmation alert when a door that exceeded the timeout is finally closed.  
The service registers with the Catalog, manages per-device configs, and supports runtime updates.

---

## Features
- Subscribes to **door_event** topics (SenML format)  
- Detects when a fridge door remains open longer than a configured threshold  
- Publishes **timeout alerts** when the threshold is exceeded  
- Publishes **door closed alerts** when the door is eventually closed  
- Auto-registers new devices with default configs  
- Supports **dynamic configuration updates** via MQTT  
- Periodically re-registers with the Catalog  

---

## MQTT Topics

**Subscriptions**
- `Group17/SmartChill/Devices/+/+/door_event` – Door events (open/closed in SenML)  
- `Group17/SmartChill/TimerUsageControl/config_update` – Config updates  

**Publications**
- `Group17/SmartChill/{device_id}/Alerts/DoorTimeout` – Timeout alert  
- `Group17/SmartChill/{device_id}/Alerts/DoorClosed` – Door closed after timeout  
- `Group17/SmartChill/TimerUsageControl/config_ack` – Config update acknowledgements  

---

## Example Messages

**Incoming Door Event (SenML, opened)**  
```json
{
  "bn": "SmartChill_A4B2C3D91E7F/",
  "bt": 1694250000.0,
  "e": [
    { "n": "door_state", "vs": "door_opened", "t": 0 }
  ]
}
```

**Door Timeout Alert**  
```json
{
  "alert_type": "door_timeout",
  "device_id": "SmartChill_A4B2C3D91E7F",
  "message": "Door has been open for 75 seconds (threshold: 60s)",
  "duration_seconds": 75,
  "threshold_seconds": 60,
  "severity": "warning",
  "timestamp": "2025-09-09T15:10:00Z",
  "service": "TimerUsageControl",
  "config_version": 3
}
```

**Door Closed Alert**  
```json
{
  "alert_type": "door_closed_after_timeout",
  "device_id": "SmartChill_A4B2C3D91E7F",
  "message": "Door closed after 75 seconds (was over 60s threshold)",
  "total_duration_seconds": 75,
  "threshold_seconds": 60,
  "over_threshold_by": 15,
  "severity": "info",
  "timestamp": "2025-09-09T15:12:00Z",
  "service": "TimerUsageControl",
  "config_version": 3
}
```

**Config Update Acknowledgement**  
```json
{
  "device_id": "SmartChill_A4B2C3D91E7F",
  "status": "updated",
  "timestamp": "2025-09-09T15:15:00Z",
  "config_version": 4
}
```

---

## Configuration

Defined in **`settings.json`**:
- **Catalog URL**: `http://catalog:8001`  
- **MQTT Broker**: `mosquitto:1883`  
- **Defaults**:  
  - Max door open time: `60s`  
  - Check interval: `5s`  
  - Alert severity: `warning`  
  - Door closed alerts: enabled  

Example per-device config:
```json
{
  "SmartChill_A4B2C3D91E7F": {
    "max_door_open_seconds": 60,
    "check_interval": 5,
    "enable_door_closed_alerts": true
  }
}
```

---

## Run
```bash
python Time_Control.py
```

The service will:  
1. Register with the Catalog  
2. Load known devices  
3. Connect to the MQTT broker  
4. Monitor door events  
5. Send alerts if thresholds are exceeded

## Modular Architecture

This service has been refactored into a modular architecture to improve maintainability and scalability.

- **`modules/utils.py`**: Helper functions for settings management and common utilities.
- **`modules/catalog_client.py`**: Handles interactions with the Catalog service (registration, device lookup).
- **`modules/mqtt_client.py`**: Manages MQTT connections, subscriptions, and publishing.
- **`modules/timer_manager.py`**: Manages door timers and triggers alerts.
- **`Time_Control.py`**: The entry point that initializes and orchestrates the modules.
