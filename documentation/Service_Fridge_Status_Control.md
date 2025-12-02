# SmartChill Fridge Status Control Service Documentation

## 1. Core Purpose
The **Fridge Status Control Service** ensures the appliance is operating within safe physical parameters. It is distinct from the Food Spoilage service (which detects rotting food) and the Energy service (which optimizes efficiency). This service detects **mechanical failures**.
-   **Monitors Temperature & Humidity**: Listens to `temperature` and `humidity` topics.
-   **Detects Malfunctions**: Identifies patterns like "High Temp + High Humidity" (Cooling Failure) or "Low Temp + High Humidity" (Defrost Issue).
-   **Alerts**: Sends "Malfunction" alerts with specific severity levels (Warning vs. Critical).

## 2. Code Explanation and Justification

### Class Structure: `FridgeStatusControl`
Like the Spoilage service, this is an **Event-Driven** microservice.

#### Pattern Detection (`detect_malfunction_patterns`)
*   **Complex Logic**: Instead of just checking if X > Threshold, it looks at the *combination* of sensors.
    *   *Scenario 1*: Temp > Max AND Humidity > Max.
        *   *Diagnosis*: **Cooling System Failure**. The compressor isn't running or is broken.
        *   *Action*: Critical Alert.
    *   *Scenario 2*: Temp < Min AND Humidity > Max.
        *   *Diagnosis*: **Defrost Cycle Issue**. Ice is building up, blocking airflow, causing humidity to spike even while it's freezing.
        *   *Action*: Warning Alert.
*   **Justification**: This "Sensor Fusion" approach provides much more valuable insights to the user than simple threshold alerts. It tells them *what* is likely wrong, not just *that* something is wrong.

#### State Tracking (`self.last_readings`)
*   **Memory**: The service remembers the last known state of each device.
*   **Reason**: MQTT messages arrive asynchronously. We might get a temperature reading now and a humidity reading 10 seconds later. To correlate them, we need to store the latest values in memory.

#### Alert Cooldown (`is_cooldown_active`)
*   **Granularity**: The cooldown is tracked *per alert type*.
*   **Benefit**: If a fridge has both a "Temperature High" issue and a "Humidity High" issue, the user gets *both* alerts. But they won't get 50 "Temperature High" alerts in a row.

## 3. Configuration (`settings.json`)

### Key Sections:
*   **`defaults`**:
    *   **`temp_min_celsius`**: 0.0°C. Below this, food freezes.
    *   **`temp_max_celsius`**: 8.0°C. Above this, bacteria grow.
    *   **`humidity_max_percent`**: 85%. Above this, mold grows and ice forms.
    *   **`alert_cooldown_minutes`**: 30. Longer than the Spoilage service because temperature changes slowly.

### Why this structure?
The configuration allows different "profiles" for different devices. A wine cooler might have a range of 10-15°C, while a meat freezer is -20 to -10°C. By changing `settings.json`, the same code can control both.
