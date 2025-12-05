# SmartChill Catalog Service

The **Catalog Service** acts as the central registry for the entire SmartChill ecosystem. It manages devices, users, and other microservices, ensuring all components can discover and communicate with each other.

## 📂 Folder Architecture

* **`main.py`**: Entry point. Initializes the CherryPy server and routes.
* **`catalog_api.py`**: Controller containing the REST API logic and request handling.
* **`catalog_utils.py`**: Helper functions for JSON file operations and data management.
* **`catalog.json`**: (Generated) The persistent database file storing system state.

## ⚙️ Functionality

* **Device Registry**: Registers new devices and stores their metadata (MAC, model, sensors).
* **User Management**: Manages users and links them to specific devices.
* **Service Discovery**: Allows other microservices to register and discover endpoints.
* **MQTT Broker Config**: Distributes broker details to connected clients.

## 📡 Interfaces

### REST APIs
* `GET /devices`: List all registered devices.
* `GET /devices/{id}`: Get details for a specific device.
* `POST /devices/register`: Register a new device.
* `GET /users/{id}`: Get user details.
* `POST /users`: Register a new user.
* `GET /services`: Discover active microservices.

### Data Format
* **JSON** is used for both storage (`catalog.json`) and API payloads.
