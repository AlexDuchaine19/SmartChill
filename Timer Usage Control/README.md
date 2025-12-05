# SmartChill Timer Usage Control

A service focused on monitoring door activity to prevent energy waste caused by leaving the fridge door open.

## 📂 Folder Architecture

* **`main.py`**: Entry point.
* **`timer_service.py`**: Manages timers for each device and the monitoring loop.
* **`timer_utils.py`**: Logic for parsing door events and calculating durations.

## ⚙️ Functionality

* **Door Monitoring**: Tracks `door_opened` and `door_closed` events.
* **Timeout Alerts**: Triggers an alert if the door remains open longer than the configured limit (default: 60s).
* **Resolution Alerts**: Notifies the user when the door is finally closed after a timeout.

## 📡 Interfaces

### MQTT Topics
* **Sub**: `Group17/SmartChill/Devices/+/+/door_event`.
* **Pub**: `Group17/SmartChill/{id}/Alerts/DoorTimeout`.

### Data Format
* **Input**: SenML (Door events).
* **Alerts**: JSON object containing open duration and threshold exceeded.