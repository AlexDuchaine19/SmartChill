# SmartChill Fridge Status Control

This service monitors the general health of the fridge, specifically focusing on temperature and humidity anomalies to prevent malfunctions.

## 📂 Folder Architecture

* **`main.py`**: Entry point.
* **`status_service.py`**: Manages MQTT connections and state tracking.
* **`status_utils.py`**: Logic for evaluating environmental conditions against safety ranges.

## ⚙️ Functionality

* **Anomaly Detection**: Detects if temperature is too high/low or humidity is excessive.
* **Complex Patterns**: Identifies specific issues like "Defrost Cycle Failure" or "Cooling System Failure" based on sensor combinations.
* **Alerting**: Publishes malfunction alerts to the user.

## 📡 Interfaces

### MQTT Topics
* **Sub**: `Group17/SmartChill/Devices/+/+/temperature` & `.../humidity`.
* **Pub**: `Group17/SmartChill/{id}/Alerts/Malfunction`.

### Data Format
* **Input**: SenML.
* **Alerts**: JSON object detailing the detected issue and severity.