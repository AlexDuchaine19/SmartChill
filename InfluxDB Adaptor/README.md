# SmartChill InfluxDB Adaptor

The **InfluxDB Adaptor** serves as the bridge between the MQTT broker and the InfluxDB time-series database. It ingests sensor data and provides a unified REST API for retrieval.

## 📂 Folder Architecture

* **`main.py`**: Entry point.
* **`influx_service.py`**: Manages MQTT subscriptions, InfluxDB connection, and the REST server.
* **`influx_utils.py`**: Handles SenML parsing and data validation logic.
* **`MyMQTT.py`**: MQTT client wrapper.

## ⚙️ Functionality

* **Data Ingestion**: Subscribes to MQTT topics and stores sensor data (temperature, humidity, etc.) into InfluxDB.
* **SenML Parsing**: Decodes SenML formatted messages.
* **Data Retrieval**: Exposes REST endpoints to query historical data easily.

## 📡 Interfaces

### MQTT Topics (Subscribed)
* `Group17/SmartChill/Devices/+/+/+`: Captures all sensor data.
* `Group17/SmartChill/Devices/+/+/door_event`: Captures door interactions.

### REST APIs
* `GET /sensors/{type}`: Retrieve sensor history.
    * *Params:* `device`, `last` (duration), `limit`.
* `GET /events`: Retrieve door open/close events.

### Data Format
* **Input**: SenML (JSON) over MQTT.
* **Storage**: InfluxDB Points (Measurements: `sensors`, `events`).
