# SmartChill Telegram Bot

The user interface for the SmartChill system. This bot orchestrates communication between the user and the IoT infrastructure.

## 📂 Folder Architecture

* **`main.py`**: Entry point. Handles startup and errors.
* **`bot_service.py`**: The central controller. Manages the MQTT loop, alerts, and bot lifecycle.
* **`telegram_handlers.py`**: Handles user interactions (commands, menus, button callbacks).
* **`catalog_client.py`**: Handles HTTP communication with the Catalog.
* **`bot_utils.py`**: Validation logic and UI text definitions.

## ⚙️ Functionality

* **User/Device Management**: Allows users to register and link their devices via MAC address.
* **Alert Notifications**: Delivers real-time MQTT alerts (Spoilage, Timer, Malfunction) to the user's chat.
* **Remote Configuration**: Provides an interactive menu to modify settings for all control services (Spoilage, Timer, Status) via MQTT.

## 📡 Interfaces

### Protocols
* **Telegram API** (Long Polling).
* **HTTP**: To communicate with the Catalog.
* **MQTT**: Subscribes to Alerts; Publishes Configuration updates.

### Interaction
* **Commands**: `/start`, `/mydevices`, `/settings`.
* **Inline Menus**: Used for device configuration and management.