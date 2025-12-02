# SmartChill Timer Usage Control Service Documentation

## 1. Core Purpose
The **Timer Usage Control Service** is a dedicated monitoring agent that tracks how long fridge doors remain open.
-   **Tracks Duration**: Starts a timer when it receives a `door_opened` event.
-   **Enforces Limits**: Checks if the duration exceeds the configured `max_door_open_seconds`.
-   **Alerts**:
    -   **Timeout Alert**: Sent immediately when the limit is breached (e.g., "Door open for 60s!").
    -   **Door Closed Alert**: Sent when the door is finally closed *if* it was previously in a timeout state (e.g., "Door finally closed after 120s").

## 2. Code Explanation and Justification

### Class Structure: `TimerUsageControl`
This service is purely **Event-Driven** but includes a **Background Monitor**.

#### The Hybrid Approach (`notify` + `monitoring_loop`)
*   **Event-Driven Part**:
    *   Listens for `door_opened` -> Starts timer in `self.device_timers`.
    *   Listens for `door_closed` -> Stops timer, calculates total duration.
*   **Polling Part**:
    *   *Problem*: If we only acted on events, we wouldn't send an alert *until the door closed*. If someone leaves the door open for 5 hours, we want to know *now*, not in 5 hours.
    *   *Solution*: A background thread (`monitoring_loop`) wakes up every few seconds to check active timers. If `current_time - start_time > limit`, it fires an alert immediately.

#### Dynamic Configuration (`handle_config_update`)
*   **Granularity**: Supports per-device configuration.
    *   *Scenario*: A commercial freezer might have a strict 30s limit. A home fridge might have a relaxed 120s limit.
*   **Access Control**: The code implements basic security logic:
    *   `admin` can change defaults and any device's config.
    *   `device_id` can only read/write its *own* config.
    *   This prevents a compromised device from messing up the settings of others.

## 3. Configuration (`settings.json`)

### Key Sections:
*   **`defaults`**:
    *   **`max_door_open_seconds`**: 60. The global default.
    *   **`check_interval`**: 5. How often the background thread runs.
*   **`devices`**:
    *   Overrides the defaults for specific device IDs.
    *   *Example*: `SmartChill_3C5AB49F2D71` has a stricter 30s limit.

### Why this structure?
This hierarchical configuration (Defaults -> Device Overrides) is a standard pattern in distributed systems. It allows for "Zero Configuration" deployment (devices work out of the box with defaults) while permitting fine-tuning where necessary.
