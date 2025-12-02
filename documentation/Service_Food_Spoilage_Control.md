# SmartChill Food Spoilage Control Service Documentation

## 1. Core Purpose
The **Food Spoilage Control Service** is a critical safety monitor. Its primary function is to detect the early signs of food rotting inside the fridge.
-   **Monitors Gas Levels**: It listens to the `gas` sensor (simulating an MQ-135 sensor sensitive to ammonia, sulfides, and benzene—common byproducts of decomposition).
-   **Alerts Users**: If gas levels exceed a safe threshold (e.g., 300 PPM), it immediately sends an alert via MQTT, which the Telegram Bot then forwards to the user.
-   **Remote Configuration**: It allows users to adjust sensitivity thresholds remotely via Telegram, without needing to restart the service.

## 2. Code Explanation and Justification

### Class Structure: `FoodSpoilageControl`
This service follows the **Event-Driven Architecture**. It does not poll the device; it waits for MQTT messages.

#### MQTT Subscription (`notify`)
*   **Topic Wildcards**: It subscribes to `Group17/SmartChill/Devices/+/+/gas`.
    *   **Justification**: The `+` wildcards allow a single service instance to monitor *all* fridges in the system simultaneously. This makes the system highly scalable.

#### Alert Logic (`handle_gas_reading`)
*   **Threshold Check**: Compares incoming PPM values against `gas_threshold_ppm`.
*   **Hysteresis/Cooldown**:
    *   **Problem**: If gas hovers around 300 PPM (e.g., 299, 301, 299, 301), a simple check would spam the user with hundreds of "Alert/Resolved" messages.
    *   **Solution**: The code implements an `alert_cooldown_minutes` (default 15 mins). Once an alert is sent, the service stays silent for 15 minutes to prevent notification fatigue.
*   **Continuous vs. One-Shot**:
    *   `enable_continuous_alerts`: If `True`, it keeps reminding the user every 15 minutes until the issue is fixed. If `False`, it only alerts once per incident.

#### Dynamic Configuration (`handle_config_update`)
*   **Feature**: Users can change the threshold (e.g., from 300 to 500 PPM) via Telegram.
*   **Implementation**: The service listens on `.../config_update`. When a message arrives, it updates its internal `self.settings` dictionary and saves it to disk (`settings.json`).
*   **Access Control**: The code checks if the requester is authorized (`admin` or the device owner) before applying changes, adding a layer of security.

## 3. Configuration (`settings.json`)

### Key Sections:
*   **`defaults`**:
    *   **`gas_threshold_ppm`**: 300. Based on typical values for spoiled meat detection with MQ sensors.
    *   **`alert_cooldown_minutes`**: 15.
*   **`devices`**:
    *   **Overrides**: This section stores specific settings for specific devices. For example, `SmartChill_3C5AB49F2D71` has a stricter threshold (100 PPM).
    *   **Justification**: This persistence ensures that user preferences (set via Telegram) survive a service restart.

### Why this structure?
The separation of `defaults` and `devices` allows the system to work "out of the box" for new devices while supporting granular customization for power users.
