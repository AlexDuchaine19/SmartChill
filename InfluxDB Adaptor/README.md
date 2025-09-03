# InfluxDB Adaptor Service

The InfluxDB Adaptor is a microservice that bridges MQTT sensor data with InfluxDB time-series database storage. It subscribes to sensor data and door events from smart refrigerators, validates device authenticity, calculates door opening durations, and provides a centralized data storage solution for analytics services.

## Features

- **MQTT Data Ingestion**: Subscribes to sensor data and door events from all registered devices
- **Device Validation**: Security layer that rejects data from unregistered devices
- **Smart Door Duration Calculation**: Automatically calculates door opening durations from open/close events
- **Time-Series Storage**: Stores data in InfluxDB with proper tagging for efficient querying
- **Auto-Configuration**: Loads configuration from Catalog service
- **Auto-Refresh**: Periodically updates registered device list to include newly registered devices

## Architecture

### Service Communication
```
Device Connector → MQTT → InfluxDB Adaptor → InfluxDB
                    ↓
               Catalog Service (device validation & configuration)
```

### Data Flow
1. Device publishes sensor data/door events to MQTT topics
2. InfluxDB Adaptor receives MQTT messages
3. Validates device is registered in Catalog
4. For door events: calculates opening durations
5. Stores processed data in InfluxDB with proper tagging
6. Provides data access for analytics services

## Database Schema

The service creates three measurements (tables) in InfluxDB:

### 1. sensor_data
Stores temperature, humidity, and gas sensor readings.

**Tags (Indexed):**
- `device_id`: Unique device identifier (e.g., "SmartChill_DDEEFF")
- `model`: Device model (e.g., "Samsung_RF28T5001SR")  
- `sensor_type`: Type of sensor ("temperature", "humidity", "gas")

**Fields (Values):**
- `value`: Sensor reading as float
- `unit`: Unit of measurement (°C, %, ppm)

**Example:**
```
sensor_data,device_id=SmartChill_DDEEFF,model=Samsung_RF28T5001SR,sensor_type=temperature value=4.5,unit="°C" 1693142400000000000
```

### 2. door_events
Stores raw door open/close events for audit purposes.

**Tags (Indexed):**
- `device_id`: Unique device identifier
- `model`: Device model
- `event_type`: "door_opened" or "door_closed"

**Fields (Values):**
- `automatic`: Boolean indicating if event was automatic or manual
- `reported_duration`: Duration reported by other services (optional)

**Example:**
```
door_events,device_id=SmartChill_DDEEFF,model=Samsung_RF28T5001SR,event_type=door_opened automatic=true 1693142400000000000
door_events,device_id=SmartChill_DDEEFF,model=Samsung_RF28T5001SR,event_type=door_closed automatic=false 1693142465000000000
```

### 3. door_metrics
Stores calculated door opening durations for analytics.

**Tags (Indexed):**
- `device_id`: Unique device identifier
- `model`: Device model

**Fields (Values):**
- `duration_seconds`: Calculated duration in seconds (float)
- `automatic`: Boolean indicating if opening was automatic

**Example:**
```
door_metrics,device_id=SmartChill_DDEEFF,model=Samsung_RF28T5001SR duration_seconds=65.0,automatic=true 1693142465000000000
```

## MQTT Topics

### Subscribed Topics
The service subscribes to all device sensor and event topics:
```
Group17/SmartChill/Devices/+/+/+
```

This pattern captures:
- `Group17/SmartChill/Devices/{model}/{device_id}/temperature`
- `Group17/SmartChill/Devices/{model}/{device_id}/humidity`
- `Group17/SmartChill/Devices/{model}/{device_id}/gas`
- `Group17/SmartChill/Devices/{model}/{device_id}/door_event`

### Message Formats

**Sensor Data:**
```json
{
    "device_id": "SmartChill_DDEEFF",
    "sensor_type": "temperature",
    "value": 4.5,
    "unit": "°C",
    "timestamp": "2024-08-27T10:30:00Z"
}
```

**Door Events:**
```json
{
    "device_id": "SmartChill_DDEEFF",
    "event_type": "door_opened",
    "timestamp": "2024-08-27T10:30:00Z",
    "automatic": true
}
```

## Security

### Device Validation
The service maintains a cache of registered devices loaded from the Catalog service:
- Only data from registered devices is stored
- Device list refreshes every 5 minutes to include newly registered devices
- Unregistered device attempts are logged with `[SECURITY]` prefix

### Validation Process
1. Load registered devices from Catalog at startup
2. For each MQTT message, check if `device_id` is in registered list
3. Accept and process if valid, reject and log if invalid
4. Automatically refresh device list every 5 minutes

## Door Duration Calculation

The service implements smart door duration calculation:

1. **Door Opened Event**: Stores opening timestamp in memory
2. **Door Closed Event**: Calculates duration and writes to `door_metrics`
3. **State Management**: Maintains internal timers for each device
4. **Comprehensive Logging**: Records ALL door openings, not just those exceeding thresholds

### Benefits
- Single source of truth for door durations
- No duplicate data
- Captures all door events regardless of duration
- Provides clean data for energy optimization analytics

## Query Examples

### Get Temperature Data
```flux
from(bucket: "smartchill")
  |> range(start: -24h)
  |> filter(fn: (r) => r["_measurement"] == "sensor_data")
  |> filter(fn: (r) => r["sensor_type"] == "temperature")
  |> filter(fn: (r) => r["device_id"] == "SmartChill_DDEEFF")
```

### Get Door Opening Durations
```flux
from(bucket: "smartchill")
  |> range(start: -24h)
  |> filter(fn: (r) => r["_measurement"] == "door_metrics")
  |> filter(fn: (r) => r["device_id"] == "SmartChill_DDEEFF")
```

### Get All Sensor Data for Device
```flux
from(bucket: "smartchill")
  |> range(start: -1h)
  |> filter(fn: (r) => r["_measurement"] == "sensor_data")
  |> filter(fn: (r) => r["device_id"] == "SmartChill_DDEEFF")
  |> pivot(rowKey:["_time"], columnKey: ["sensor_type"], valueColumn: "_value")
```

## Configuration

### Environment Variables
- `PYTHONUNBUFFERED=1`: Ensures logs are visible in Docker

### InfluxDB Connection
- **URL**: `http://influxdb:8086`
- **Token**: `smartchill-token`
- **Organization**: `smartchill`
- **Bucket**: `smartchill`

### MQTT Connection
- **Broker**: `mosquitto:1883`
- **Client ID**: `influxdb_adaptor_{timestamp}`

## API Methods

The service provides internal methods for data retrieval:

### `get_sensor_data(sensor_type, device_id=None, last_hours=24)`
Retrieves sensor data with optional device filtering.

### `get_door_metrics(device_id=None, last_hours=24)`
Retrieves door duration metrics with optional device filtering.

## Logging

The service provides detailed logging with prefixes:
- `[INIT]`: Startup and configuration
- `[CONFIG]`: Device list management
- `[INFLUX]`: Database operations
- `[MQTT]`: MQTT connectivity
- `[DOOR]`: Door event processing
- `[SECURITY]`: Device validation failures
- `[ERROR]`: Error conditions
- `[WARN]`: Warning conditions

## Dependencies

- `influxdb-client==1.36.1`: InfluxDB 2.x client library
- `paho-mqtt==1.6.1`: MQTT communication
- `requests==2.31.0`: HTTP requests to Catalog service

## Docker Integration

The service runs in Docker with:
- Health checks for InfluxDB and Catalog connectivity
- Automatic restart on failure
- Network connectivity to other SmartChill services
- No console interaction (Docker-friendly)

## Data Retention

Data retention is managed by InfluxDB configuration. Consider setting appropriate retention policies based on storage requirements and analytics needs.

## Troubleshooting

### Common Issues
1. **Device data rejected**: Check if device is registered in Catalog
2. **InfluxDB connection failed**: Verify InfluxDB service is running
3. **MQTT connection failed**: Verify Mosquitto broker is accessible
4. **Missing door durations**: Check for orphaned door_opened events without corresponding door_closed

### Debug Queries
Check device registration:
```bash
curl http://catalog:8001/devices
```

Check InfluxDB health:
```bash
curl http://influxdb:8086/health
```