# SmartChill Device Connector (Fridge Simulator) Documentation

## 1. Core Purpose
The **Device Connector** acts as a digital twin and simulator for the smart fridge. Since we don't have physical hardware for this project, this service:
-   **Simulates Hardware**: It generates realistic sensor data (temperature, humidity, gas, light) based on a thermal model.
-   **Connects to the Cloud**: It implements the MQTT client that connects the "device" to the SmartChill platform.
-   **Handles Commands**: It listens for remote commands (e.g., "open door", "simulate spoilage") to allow for system testing and demonstration.

It effectively "tricks" the rest of the system into thinking it's talking to a real smart fridge.

## 2. Code Explanation and Justification

### Class Structure: `FridgeSimulator`
The entire logic is encapsulated in the `FridgeSimulator` class in `Fridge.py`.

#### Thermal Model (`simulate_thermal_dynamics`)
Instead of random numbers, the simulator uses a basic physics-based model:
*   **Cooling Rate**: When the compressor is ON, temperature drops by `3.0°C/hour`.
*   **Warming Rate**: When the compressor is OFF, temperature rises by `0.5°C/hour` (insulation loss).
*   **Door Open Rate**: If the door is open, temperature rises much faster (`3.0°C/hour`).
*   **Justification**: This creates realistic data patterns (sawtooth waves) that allow the *Energy Optimization* service to actually perform duty cycle analysis. Random data would make optimization algorithms useless.

#### Data Format: SenML
The simulator publishes data using the **SenML (Sensor Measurement Lists)** format.
*   **Format**: `{"bn": "device_id/", "bt": timestamp, "e": [{"n": "temp", "v": 4.0, "u": "Cel"}]}`
*   **Justification**: SenML is a standard IETF format for IoT. It's concise, self-describing (includes units and timestamps), and supports batching. Using a standard format ensures interoperability with the *InfluxDB Adaptor*.

#### MQTT Interaction
*   **`MyMQTT` Helper**: Uses a custom wrapper around `paho-mqtt` to simplify connection and subscription logic.
*   **Topics**: It constructs topics dynamically using the template from `settings.json` (e.g., `Group17/SmartChill/Devices/...`). This avoids hardcoding and allows the topic structure to change via configuration.

#### Simulation Features
*   **`generate_realistic_data`**: Introduces noise and correlations (e.g., humidity rises when the door opens) to make the data "messy" enough for real-world testing.
*   **`_get_door_open_probability`**: Uses a time-of-day model (higher probability during meal times) to simulate human behavior automatically.

## 3. Configuration (`settings.json`)

### Key Sections:
*   **`catalog_url`**: The address of the Catalog service. The device *must* register here first to get its ID and topics.
*   **`deviceInfo`**:
    *   **`mac_address`**: The physical identifier. Used to claim identity during registration.
    *   **`sensors`**: Lists the hardware sensors this specific unit has.
*   **`sampling_intervals`**: Controls how often each sensor publishes data (e.g., `temperature`: 300s). This allows balancing data resolution with network bandwidth.
*   **`mqtt_data`**:
    *   **`topic_template`**: The pattern for constructing MQTT topics.
    *   **`include_events`**: Specifies which asynchronous events (like "door_opened") should be published immediately, bypassing the sampling interval.

### Why this structure?
This configuration allows the same code to run on different "devices" just by changing the `mac_address` and `sensors` list. It supports the "write once, deploy everywhere" philosophy of the Device Connector.
