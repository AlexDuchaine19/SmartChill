# SmartChill InfluxDB Adaptor Service Documentation

## 1. Core Purpose
The **InfluxDB Adaptor Service** acts as the "Historian" of the platform. It bridges the gap between the real-time MQTT world and the persistent database world.
-   **Ingests Data**: Subscribes to *all* sensor topics (`temperature`, `humidity`, `light`, `gas`, `door_event`).
-   **Stores Data**: Writes this data into **InfluxDB v2**, a time-series database optimized for IoT data.
-   **Serves Data**: Provides a REST API for other services (like Data Analysis and the Dashboard) to query historical data without needing to know InfluxDB's complex query language (Flux).

## 2. Code Explanation and Justification

### Class Structure: `InfluxDBAdaptor`
This service is a hybrid **MQTT Subscriber + REST API**.

#### Data Ingestion (`notify` -> `store_sensor_data`)
*   **SenML Parsing**: The service expects data in the **SenML** (Sensor Measurement Lists) format.
    *   *Justification*: SenML is a standard. It allows a single message to contain multiple readings with units and timestamps.
*   **Validation**: Before writing to the DB, it checks if values are sane (e.g., Temperature between -50 and 100°C). This prevents "garbage data" from corrupting the history.

#### Batch Writing (`batch_writer_loop`)
*   **The Problem**: Writing to the database for *every single* MQTT message is inefficient and can overwhelm the DB network connection.
*   **The Solution**:
    1.  Incoming data is pushed to a thread-safe `queue`.
    2.  A background thread wakes up every 10 seconds (or when the queue is full).
    3.  It writes all queued points in a single HTTP batch request to InfluxDB.
*   **Benefit**: drastically reduces network overhead and improves throughput.

#### REST API (`query_door_events_from_influx`)
*   **Abstraction**: The `query_api` methods hide the complexity of InfluxDB's **Flux** language.
    *   *Example*: A client just asks for `GET /door_events?last=24h`. The Adaptor translates this into: `from(bucket: "smartchill") |> range(start: -24h) |> filter(...)`.
*   **Decoupling**: If we switch from InfluxDB to PostgreSQL later, we only rewrite this Adaptor. The Dashboard and Analysis services (which use the REST API) won't need to change.

## 3. Configuration (`settings.json`)

### Key Sections:
*   **`influxdb`**:
    *   **`url`**: `http://influxdb:8086`. The address of the database container.
    *   **`token_env_var`**: `INFLUX_TOKEN`.
    *   **Justification**: We do *not* hardcode the secret token in JSON. The code looks for it in the environment variables (set by Docker Compose), which is a security best practice.
*   **`defaults`**:
    *   **`batch_size`**: 100.
    *   **`flush_interval_seconds`**: 10.
    *   These control the batching logic mentioned above.

### Why this structure?
The separation of database connection details (`influxdb` section) from application logic allows easy deployment in different environments (dev vs. prod) without code changes.
