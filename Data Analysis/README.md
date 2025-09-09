# SmartChill Data Analysis Service

The **Data Analysis Service** processes raw sensor data from SmartChill devices and generates **statistical insights, usage patterns, and trends**.  
It communicates with the Catalog Service for device information and with the InfluxDB Adaptor for historical data.

---

## Endpoints & Example Responses

### Health & Status

**GET /health** – Check if the service is running  
```json
{
  "status": "healthy",
  "service": "Data Analysis",
  "timestamp": "2025-09-09T14:10:00Z",
  "known_devices": 5
}
```

**GET /status** – Detailed service status  
```json
{
  "service_id": "DataAnalysis",
  "status": "running",
  "rest_api_active": true,
  "known_devices": 5,
  "supported_periods": ["1h","6h","12h","1d","7d","1m","3m"],
  "config_version": 2
}
```

---

### Analysis

**GET /analyze/{device_id}?period={duration}&metrics={list}** – Perform full analysis (temperature, usage, trends)  
```json
{
  "device_id": "SmartChill_A4B2C3D91E7F",
  "period": "7d",
  "metrics_requested": ["temperature","usage_patterns","trends"],
  "analysis_timestamp": "2025-09-09T14:12:00Z",
  "temperature_analysis": {
    "avg_temperature": 4.2,
    "min_temperature": 2.1,
    "max_temperature": 6.5,
    "temperature_variance": 0.45,
    "stability_score": 85,
    "out_of_range_time_percent": 3.2,
    "data_points": 150
  },
  "usage_analysis": {
    "total_openings": 42,
    "avg_daily_openings": 6.0,
    "avg_duration_seconds": 45.2,
    "max_duration_seconds": 150,
    "efficiency_score": 80,
    "events_analyzed": 42
  },
  "trends": {
    "temperature_trend": "stable",
    "usage_trend": "increasing",
    "period_analyzed": "7d"
  },
  "data_summary": {
    "temperature_points": 150,
    "door_events": 42,
    "period_days": 7
  }
}
```

---

### Trends

**GET /trends/{device_id}?period={duration}** – Analyze only temperature and usage trends  
```json
{
  "device_id": "SmartChill_A4B2C3D91E7F",
  "period": "1m",
  "trends": {
    "temperature_trend": "decreasing",
    "usage_trend": "stable",
    "period_analyzed": "1m"
  },
  "generated_at": "2025-09-09T14:15:00Z"
}
```

---

### Patterns

**GET /patterns/{device_id}?type={usage|temperature|efficiency}&period={duration}** – Get specific analysis patterns  
- `usage` → door usage patterns  
- `temperature` → temperature statistics  
- `efficiency` → combined score of temperature stability and door usage  

Example response (`efficiency` type):  
```json
{
  "device_id": "SmartChill_A4B2C3D91E7F",
  "type": "efficiency",
  "period": "7d",
  "patterns": {
    "overall_efficiency": 82.5,
    "temperature_efficiency": 85,
    "usage_efficiency": 80,
    "factors": {
      "temperature_stability": true,
      "optimal_door_usage": true,
      "minimal_out_of_range": true
    }
  },
  "generated_at": "2025-09-09T14:18:00Z"
}
```

---

## Configuration

Settings are defined in **`settings.json`**:
- **Catalog URL**: `http://catalog:8001`  
- **InfluxDB Adaptor**: `http://influxdb_adaptor:8002`  
- **Supported periods**: `["1h","6h","12h","1d","7d","1m","3m"]`  
- **Default period**: `24h`  

---

## Run
```bash
python Data_Analysis.py
# Service runs at http://localhost:8004
```
