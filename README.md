# SmartChill - IoT Fridge Monitoring Platform 🧊

**Version:** 1.0.0
**Team:** Alexander Duchaine (346096), Luca Marchese (261683)
**Course:** Programming for IoT (Politecnico di Torino)

## 🎯 Project Overview

SmartChill is an IoT platform designed to monitor the status and usage patterns of smart refrigerators. It aims to enhance food management, improve energy efficiency awareness, and provide timely alerts for potential issues like spoilage or malfunctions. The system utilizes a microservice architecture communicating via MQTT and REST APIs, with data persistence in InfluxDB and user interaction facilitated through a Telegram Bot and a Node-RED dashboard.

## ✨ Key Features

* **Real-time Monitoring:** Tracks key fridge parameters including Temperature, Humidity, Air Quality (Gas), Internal Light, and Door State.
* **Usage Pattern Analysis:** Monitors door opening frequency and duration to identify inefficient usage habits.
* **Energy Optimization:**
    * Estimates current energy consumption (kWh/day) based on real-time compressor duty cycle analysis derived from temperature data.
    * Provides a breakdown of energy usage (base runtime vs. door opening penalty).
    * Generates actionable recommendations to improve efficiency.
    * Offers personalized 7-day energy consumption forecasts based on historical data (requires sufficient data for training).
* **Intelligent Alerting:** Sends notifications via Telegram for:
    * **Malfunctions:** Temperature or humidity outside safe ranges.
    * **Food Spoilage Risk:** High gas sensor readings.
    * **Door Timeout:** Fridge door left open for too long.
* **User & Device Management:** Secure user registration and device linking via Telegram, verified using the device's MAC address.
* **Data Visualization:** Interactive dashboard built with Node-RED displaying real-time sensor data, health status, energy analysis, and historical trends.
* **Persistent Storage:** Sensor readings and events are stored in an InfluxDB time-series database for historical analysis.

---

## 🏗️ Architecture

The platform follows a microservice architecture orchestrated using Docker Compose. Each service has been refactored into a **modular architecture** to ensure scalability, maintainability, and separation of concerns.

### Modular Service Structure

Every Python-based service in the project now follows a consistent directory structure:

*   **`modules/`**: Contains the core logic and helper classes.
    *   **`utils.py`**: Shared utility functions (e.g., settings management).
    *   **`catalog_client.py`**: Handles all interactions with the Catalog Service (registration, lookups).
    *   **`mqtt_client.py`**: Encapsulates MQTT connection, subscription, and publishing logic.
    *   **`[logic_module].py`**: Contains the specific business logic for the service (e.g., `spoilage_monitor.py`, `analyzer.py`).
*   **`[Service_Name].py`**: The main entry point script that initializes and orchestrates the modules.
*   **`settings.json`**: Configuration file for the service.

### Service Descriptions

* **`Device Connector (Fridge.py)`:** Simulates a smart fridge, generating sensor data (Temperature, Humidity, Gas, Light) and door events. Publishes data via MQTT in SenML format. Receives simulation commands (e.g., `door_open`, `malfunction_start`) via MQTT.
* **`Catalog (Catalog.py)`:** Central registry for devices, users, and services. Manages device registration (based on MAC address), user registration (linked to Telegram `chat_id`), device assignments, and service discovery. Provides a REST API.
* **`Mosquitto`:** MQTT Broker facilitating asynchronous communication between services.
* **`InfluxDB`:** Time-series database for storing all sensor readings and door events.
* **`InfluxDB Adaptor (InfluxDB_Adaptor.py)`:** Subscribes to all sensor and event topics on MQTT, validates data, and writes it to InfluxDB. Exposes a REST API for querying historical data (used by Data Analysis and Energy Optimization).
* **`Fridge Status Control (StatusControl.py)`:** Subscribes to Temperature and Humidity data. Analyzes readings against configured thresholds. Publishes Malfunction alerts via MQTT if anomalies are detected.
* **`Food Spoilage Control`:** Subscribes to Gas sensor data. Analyzes air quality. Publishes Spoilage alerts via MQTT if spoilage conditions are detected. 
* **`Timer Usage Control`:** Subscribes to Door Event data (originally planned for Light sensor). Tracks door open duration. Publishes Door Timeout alerts via MQTT if the door stays open too long. Publishes Door Closed notifications.
* **`Data Analysis (Data_Analysis.py)`:** Provides a REST API to retrieve processed historical data. Fetches raw data from the `InfluxDB Adaptor`, calculates statistics (average, min/max, variance), scores (stability, efficiency), and trends.
* **`Energy Optimization (Optimizer.py)`:** Provides a REST API for energy analysis. Fetches real historical temperature and door event data from the `InfluxDB Adaptor`. Performs duty cycle analysis, estimates current kWh consumption, generates recommendations, and trains/uses a personalized ML model for future consumption forecasting.
* **`Telegram Bot (TelegramBot.py)`:** Acts as the primary user interface. Handles user registration/login via MAC address verification. Allows users to manage their devices. Subscribes to MQTT Alert topics and forwards notifications to the appropriate linked user via Telegram.
* **`Node-RED`:** Provides the web dashboard. Fetches data periodically from the `Data Analysis` and `Energy Optimization` REST APIs for visualization. Allows sending simulation commands to the `Device Connector` via MQTT.

---

## 🚀 Setup & Running with Docker

### Prerequisites

* [Docker](https://docs.docker.com/get-docker/) installed.
* [Docker Compose](https://docs.docker.com/compose/install/) installed.

### Configuration

1.  **Telegram Bot Token:** Edit the `Telegram Bot/settings.json` file and replace `"YOUR_TELEGRAM_BOT_TOKEN_HERE"` with your actual Telegram Bot token obtained from BotFather.
2.  **(Optional) Fridge MAC Address:** Edit the `Device Connector/settings.json` file to set the desired MAC address for the simulated fridge.
3.  **(Optional) Other Settings:** Review settings files (`settings.json`) within each service directory for default thresholds, intervals, etc., if needed.

### Build & Run

1.  **Open Terminal:** Navigate to the root directory containing the `docker-compose.yml` file.
2.  **Build Images:**
    ```bash
    docker-compose build
    ```
3.  **Start Services:**
    ```bash
    docker-compose up -d
    ```
    *(The `-d` flag runs containers in the background)*

### Accessing Interfaces

* **Node-RED Dashboard:** `http://localhost:1880/ui` (or your machine's IP)
* **Node-RED Editor:** `http://localhost:1880`
* **InfluxDB UI:** `http://localhost:8086` (Use credentials from `docker-compose.yml`: org=`smartchill`, user=`admin`, pass=`smartchill123`, token=`smartchill-token`)
* **Catalog API (Example):** `http://localhost:8001/info`
* **Service APIs:** Ports `8002` (Influx Adaptor), `8003` (Optimizer), `8004` (Data Analysis) are exposed.

### Stopping Services

```bash
docker-compose down
```
