# SmartChill Catalog Service Documentation

## 1. Core Purpose
The **Catalog Service** acts as the central registry and "brain" of the SmartChill IoT platform. Its primary responsibilities are:
-   **Service Discovery**: It maintains a live registry of all active microservices (e.g., Energy Optimization, Food Spoilage Control), allowing them to discover each other's endpoints dynamically.
-   **Device Management**: It handles the registration of smart devices (fridges), assigning them unique IDs and MQTT topics.
-   **User Management**: It links Telegram users to their devices, enabling personalized control and alerts.
-   **System Configuration**: It serves as the single source of truth for system-wide configurations, such as the MQTT broker address and supported device models.

By centralizing this information, the Catalog decouples the microservices. For example, the *Telegram Bot* doesn't need to know the IP address of the *Energy Optimization* service; it simply asks the Catalog.

## 2. Code Explanation and Justification

### Framework Choice: CherryPy
The service is built using **CherryPy**, a minimalist Python web framework.
*   **Justification**: CherryPy is lightweight, multi-threaded, and stable. It allows for quick creation of RESTful APIs without the overhead of larger frameworks like Django. Its built-in WSGI server is robust enough for this microservice architecture.

### Key Components (`Catalog.py`)

#### `CatalogAPI` Class
This class exposes the REST API endpoints.
*   **`register_device`**: Handles device registration. It generates a unique `device_id` based on the MAC address to ensure consistency. It also generates specific MQTT topics for the device based on its model, ensuring a standardized topic structure across the system.
*   **`register_service`**: Allows other microservices to register themselves on startup. This implements the "Service Discovery" pattern.
*   **`link_telegram`**: A critical endpoint that associates a Telegram Chat ID with a user account, bridging the gap between the physical device and the user interface.

#### Data Persistence
*   **JSON Storage**: The catalog state is stored in `catalog.json`.
*   **Justification**: For a student/prototype project, a JSON file is simple to inspect, debug, and backup. It avoids the complexity of setting up and maintaining a separate SQL database container. The `load_catalog()` and `save_catalog()` helper functions ensure that data is persisted to disk immediately after any change.

#### Error Handling
*   **`http_error` helper**: Standardizes error responses (400, 404, 500) with JSON payloads, making debugging easier for client services.

## 3. Configuration (`catalog.json`)

The `catalog.json` file serves as both the initial configuration and the persistent database.

### Key Sections:
*   **`broker`**: Defines the MQTT broker's IP (`mosquitto`) and port (`1883`). All services fetch this config to connect to the message bus.
*   **`deviceModels`**: Defines the capabilities of supported fridges (e.g., `Samsung_RF28T5001SR`).
    *   **`sensors`**: Lists available sensors (temp, humidity, gas).
    *   **`power_consumption`**: Contains specific data (watts, penalties) used by the *Energy Optimization* service to calculate costs.
    *   **`mqtt`**: Defines the topic template (e.g., `Group17/SmartChill/Devices/{model}/{device_id}/{sensor}`), ensuring all devices publish to a predictable structure.
*   **`servicesList`**: A dynamic list of registered services. Each entry contains the service's `endpoints`, allowing others to find its REST API or MQTT topics.
*   **`devicesList`**: Stores registered devices, their MAC addresses, and their assigned owners.
*   **`usersList`**: Stores user accounts and their linked Telegram Chat IDs.

### Why this structure?
Storing `deviceModels` here allows the system to support new fridge models simply by updating this JSON file, without changing any code in the *Energy Optimization* or *Device Connector* services. This makes the system highly extensible.
