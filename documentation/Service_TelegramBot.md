# SmartChill Telegram Bot Service Documentation

## 1. Core Purpose
The **Telegram Bot Service** is the primary "User Interface" for the SmartChill platform. While the Node-RED dashboard is for visualization, the Bot is for **interaction and alerts**.
-   **User Management**: Handles user registration and links Telegram accounts to SmartChill user IDs.
-   **Device Management**: Allows users to "claim" fridges by entering their MAC address.
-   **Alert System**: Delivers real-time push notifications for critical events (Door Left Open, High Temp, Spoilage Risk).
-   **Remote Control**: Provides a menu-driven interface to change settings on other microservices (e.g., changing the "Door Open Timeout" from 60s to 120s).

## 2. Code Explanation and Justification

### Class Structure: `TelegramBot`
This service uses the `telepot` library for the Bot API and `MyMQTT` for system communication.

#### The State Machine (`set_status`, `handle_...`)
*   **Problem**: Telegram is stateless. If a user types "120", how do we know if they mean "120 seconds timeout" or "Device ID 120"?
*   **Solution**: A per-user State Machine (`self.user_states`).
    *   *Example*: When a user clicks "Rename Device", their state becomes `waiting_for_device_rename`. The next text message they send is interpreted as the new name, not a command.

#### Dynamic Configuration (`cb_service_menu`)
*   **Architecture**: The Bot does *not* store device settings. It fetches them on-demand.
*   **Flow**:
    1.  User clicks "Settings" -> "Door Timer".
    2.  Bot publishes a `config_get` request via MQTT to the *Timer Usage Control* service.
    3.  Timer service replies with JSON config.
    4.  Bot dynamically builds a UI with buttons for each setting (e.g., "Timeout: 60s [Edit]").
*   **Justification**: This makes the Bot "dumb" and the services "smart". If we add a new setting to the Timer service, the Bot doesn't need a code update; it just renders whatever JSON it receives.

#### Alert Routing (`notify`)
*   **Topic Subscription**: Subscribes to `.../Alerts/+`.
*   **Logic**: When an alert arrives (e.g., "Door Open!"), the Bot:
    1.  Extracts the `device_id`.
    2.  Queries the *Catalog* to find which user owns this device.
    3.  Looks up the user's `telegram_chat_id`.
    4.  Forwards the message to that specific user.
*   **Privacy**: This ensures users only get alerts for *their* fridges.

## 3. Configuration (`settings.json`)

### Key Sections:
*   **`telegram`**:
    *   **`TOKEN`**: The API token from BotFather.
*   **`endpoints`**:
    *   **`MQTT Publish`**: Defines the template `.../{service_name}/{device_id}/config_update`. This template allows the Bot to construct topics dynamically for *any* service.

### Why this structure?
The dynamic topic construction means the Bot is generic. It can control the "Food Spoilage" service just as easily as the "Timer" service without hardcoding their specific topic names in the Python code.
