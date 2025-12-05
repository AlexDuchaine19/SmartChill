# SmartChill Fridge Simulator

This component simulates a smart IoT fridge. It generates realistic sensor data based on a physical thermal model and user interaction patterns.

## 📂 Folder Architecture

* **`main.py`**: Entry point. Starts the simulation loop.
* **`fridge_service.py`**: Manages the device state (compressor, door), simulation loops, and MQTT publishing.
* **`fridge_utils.py`**: Logic for thermal calculations and SenML payload generation.

## ⚙️ Functionality

* **Thermal Model**: Simulates temperature changes based on compressor cycles and door state.
* **Simulation**: Generates data for Temperature, Humidity, Gas (spoilage), and Light.
* **Interactivity**: Accepts remote MQTT commands to simulate faults (e.g., "leave door open").

## 📡 Interfaces

### MQTT Topics (Published)
* `Group17/SmartChill/Devices/{model}/{id}/{sensor}`: Periodic sensor readings.
* `Group17/SmartChill/Devices/{model}/{id}/door_event`: Asynchronous door events.

### MQTT Topics (Subscribed)
* `Group17/SmartChill/Commands/{id}/simulation`: Receives commands (e.g., `spoilage_start`, `door_open`).

### Data Format
* **SenML (JSON)**: All telemetry is published using the SenML standard.