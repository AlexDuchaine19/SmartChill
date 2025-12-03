# SmartChill Telegram Bot Service

The **Telegram Bot Service** provides a user interface for interacting with the SmartChill system via Telegram. Users can receive alerts, check device status, and manage their smart fridges.

## Features

- **Alert Notifications**: Receives alerts from other services (Spoilage, Door Timeout, Malfunction) and forwards them to subscribed users.
- **Status Checks**: Allows users to query the current status of their devices (temperature, humidity, etc.).
- **User Management**: Handles user registration and subscription to specific devices.
- **Command Interface**: Supports commands like `/start`, `/status`, `/help`.

## Architecture

The service subscribes to MQTT alert topics and uses the Telegram Bot API to send messages. It also queries the Catalog and other services for status information.

## Modular Architecture

This service has been refactored into a modular architecture to improve maintainability and scalability.

- **`modules/utils.py`**: Helper functions for settings management and common utilities.
- **`modules/catalog_client.py`**: Handles interactions with the Catalog service.
- **`modules/mqtt_client.py`**: Manages MQTT connections and subscriptions to alert topics.
- **`modules/bot_handlers.py`**: Manages Telegram bot interactions and command handling.
- **`TelegramBot.py`**: The entry point that initializes and orchestrates the modules.

## Configuration

Defined in **`settings.json`**:
- **Telegram Token**: The API token for the bot.
- **MQTT Broker**: Connection details for the MQTT broker.
- **Catalog URL**: URL of the Catalog service.

## Run

```bash
python TelegramBot.py
```
