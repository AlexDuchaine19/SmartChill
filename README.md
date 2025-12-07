# SmartChill - IoT Fridge Monitoring Platform 🧊

**Version:** 1.0.0
**Team:** Alexander Duchaine (346096), Luca Marchese (261683)
**Course:** Programming for IoT (Politecnico di Torino)

# ❄️ SmartChill - Intelligent IoT Fridge Management System

**SmartChill** is a comprehensive IoT ecosystem designed to monitor, control, and optimize smart refrigerators. Built on a microservices architecture, it leverages MQTT for real-time communication, InfluxDB for time-series data storage, and Machine Learning for energy optimization.

The system provides real-time alerts via Telegram, visual dashboards via Node-RED, monitors food spoilage, ensures door security, and predicts energy consumption patterns.

---

## 🏗️ System Architecture

The project follows a **Service-Oriented Architecture (SOA)**. The components interact via two main communication protocols:

1. **MQTT (Pub/Sub):** Used for real-time telemetry (sensors), asynchronous events (door opening), and immediate alerts.  
2. **REST API (HTTP):** Used for service discovery (Catalog), retrieving historical data, and complex analytics.

### Architectural Layers

- **Device Layer:** Smart Refrigerators (simulated) that publish environmental data (Temperature, Humidity, Gas) and events.
- **Infrastructure Layer:** Mosquitto (MQTT Broker) for messaging and InfluxDB for data persistence.
- **Core Services Layer:**  
  - **Catalog:** Central registry for devices, users, and services.  
  - **InfluxDB Adaptor:** Bridge between MQTT and the database.
- **Control Layer:** Specialized microservices (Spoilage, Timer, Status) that analyze real-time data streams to trigger alerts.
- **Intelligence Layer:**  
  - **Data Analysis:** Performs historical and statistical batch analysis.  
  - **Energy Optimizer:** Machine learning predictions for energy usage.
- **User Interface Layer:**  
  - **Telegram Bot:** Chat-based interface for alerts and configuration.  
  - **Node-RED:** Visual dashboard for real-time monitoring and alternative device registration.

---

## 🧩 Microservices Overview

| Service | Description | Protocol |
|--------|-------------|----------|
| **Catalog Service** | The central registry. Keeps track of all connected devices, users, and active services. | HTTP |
| **Fridge Simulator** | Simulates a physical smart fridge. Generates realistic sensor data based on thermal physics and user behavior. | MQTT |
| **InfluxDB Adaptor** | Subscribes to sensor topics and stores data into InfluxDB. Provides APIs for data retrieval. | MQTT / HTTP |
| **Telegram Bot** | Primary mobile interface. Allows users to register devices, view status, change settings, and receive alerts. | HTTP / MQTT |
| **Node-RED** | Visual dashboard for graphing real-time sensor data and an alternative entry point for device registration. | MQTT / HTTP |
| **Spoilage Control** | Monitors gas levels (VOCs) to detect rotting food and triggers immediate alerts. | MQTT |
| **Timer Control** | Monitors door status. Triggers alerts if the door is left open for too long. | MQTT |
| **Status Control** | Monitors temperature and humidity for anomalies (e.g., “Defrost Cycle Failure”). | MQTT |
| **Data Analysis** | Analyzes historical data to provide usage statistics and trends via API. | HTTP |
| **Energy Optimizer** | Uses **Machine Learning (Linear Regression)** to predict future energy usage and suggest efficiency improvements. | HTTP |

---

## 📡 Communication & Data Format

### **1. Data Format (SenML)**

All sensor data sent over MQTT follows the **SenML (Sensor Measurement Lists)** JSON standard.

**Example Payload:**

```json
{
  "bn": "SmartChill_A1B2C3/",
  "bt": 1678886400,
  "e": [
    { "n": "temperature", "u": "Cel", "v": 4.5 },
    { "n": "humidity", "u": "%RH", "v": 60 }
  ]
}
```

---

### **2. MQTT Topic Structure**

The system uses hierarchical MQTT topics:

- **Sensors:**  
  `Group17/SmartChill/Devices/{model}/{device_id}/{sensor_type}`

- **Events:**  
  `Group17/SmartChill/Devices/{model}/{device_id}/door_event`

- **Alerts:**  
  `Group17/SmartChill/{device_id}/Alerts/{alert_type}`

- **Config:**  
  `Group17/SmartChill/{service_name}/{device_id}/config_update`

---

## 🚀 How to Run

### **Prerequisites**

- Python 3.9+  
- MQTT Broker (Mosquitto) running on port **1883**  
- InfluxDB v2 running on port **8086**  
- Node-RED running on port **1880**

---

### **Installation**

Clone the repository:

```bash
git clone https://github.com/AlexDuchaine19/SmartChill.git
cd SmartChill
```

Install Python dependencies (each service folder may contain additional ones):

```bash
pip install cherrypy requests paho-mqtt telepot influxdb-client scikit-learn numpy
```

---

### **Execution Order**

To ensure proper system discovery, start services in this order:

1. **Infrastructure:**  
   Start Mosquitto, InfluxDB, and Node-RED.

2. **Catalog Service:**  
   ```bash
   python Catalog/main.py
   ```

3. **InfluxDB Adaptor:**  
   ```bash
   python InfluxDB_Adaptor/main.py
   ```

4. **Control Services:**  
   Run `main.py` in:  
   - Spoilage_Control  
   - Timer_Control  
   - Status_Control  

5. **Analytics Services:**  
   Run `main.py` in:  
   - Data_Analysis  
   - Energy_Optimization  

6. **Interfaces:**  
   ```bash
   python TelegramBot/main.py
   ```  
   Ensure Node-RED flows are active.

7. **Device Simulator:**  
   ```bash
   python Fridge_Device/main.py
   ```

---

## 👤 User Guide

### **Using Telegram**

- **/start** — Begin interaction  
- **Device Registration:** Enter the MAC address shown in the Fridge Simulator console  
- **If registered via Node-RED:** The bot will ask for your username to link accounts  
- **/mydevices** — View fridge status  
- **⚙️ Settings:** Modify thresholds (Max Temp, Door Timeout, etc.)

---

### **Using Node-RED**

Access:

👉 **http://localhost:1880/dashboard/login**
- Insert username and mac address of device connector

**Dashboard Features:**

- Real-time charts (Temperature, Humidity, Gas)
- Door status indicators
- Alternative device registration form

---

## 🔔 Alerts

You will automatically receive Telegram notifications for:

- 🚪 **Door left open**  
- 🔥 **Food spoilage detection** (VOC spike)  
- 🌡 **Temperature anomalies**  

---

## 🛠 Configuration

Each service contains a `settings.json` file.

Important files:

- `Catalog/catalog.json` — System state (do not modify while running)  
- `TelegramBot/settings.json` — Requires your Telegram Bot Token  
- `InfluxDB_Adaptor/settings.json` — Requires InfluxDB Token, Org, Bucket  

---

## 👥 Authors

**Group 17 — IoT Project**

