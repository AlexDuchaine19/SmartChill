# SmartChill Food Spoilage Control

A monitoring service dedicated to detecting potential food spoilage by analyzing gas sensor readings (VOCs/odors).

## 📂 Folder Architecture

* **`main.py`**: Entry point.
* **`spoilage_service.py`**: Manages MQTT connections, device configuration, and alert dispatching.
* **`spoilage_utils.py`**: Logic for threshold evaluation and SenML parsing.

## ⚙️ Functionality

* **Gas Monitoring**: Continuously checks gas PPM levels against configured thresholds.
* **Alerting**: Sends alerts via MQTT when thresholds are breached.
* **Dynamic Config**: Allows remote adjustment of thresholds and alert frequency via MQTT.

## 📡 Interfaces

### MQTT Topics
* **Sub**: `Group17/SmartChill/Devices/+/+/gas` (Sensor readings).
* **Sub**: `.../config_update` (Remote configuration).
* **Pub**: `Group17/SmartChill/{id}/Alerts/Spoilage` (Alerts).

### Data Format
* **Input**: SenML (Gas readings).
* **Alerts**: JSON object containing severity, timestamp, and recommended actions.