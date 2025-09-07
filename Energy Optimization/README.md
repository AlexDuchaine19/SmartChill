# SmartChill Energy Optimization Service

## Overview

The Energy Optimization Service is an advanced analytics microservice that uses machine learning to analyze energy consumption patterns of SmartChill refrigerators and provide actionable optimization recommendations. The service combines real-time sensor data analysis with predictive modeling to help users reduce energy consumption and improve appliance efficiency.

## Key Features

### 🤖 Machine Learning Predictions
- **Multi-variable regression** using configurable features (door openings, temperature patterns, usage behavior)
- **7-day energy consumption forecasting** with confidence intervals
- **Automatic model retraining** every 72 hours with new data
- **Model accuracy tracking** using MAE and R² metrics

### 📊 Advanced Energy Analysis
- **Sophisticated power consumption modeling** based on:
  - Base power consumption
  - Door opening penalties
  - Temperature variance impacts
  - Efficiency degradation factors
- **Real-time feature extraction** from historical sensor data
- **Anomaly detection** with configurable sensitivity thresholds

### 💡 Intelligent Recommendations
- **Behavioral optimization tips** (reduce door openings, minimize open duration)
- **Temperature setting recommendations** for optimal efficiency
- **Energy target monitoring** with deviation alerts
- **Personalized suggestions** based on user behavior profiles

### 🔄 Hybrid Triggering System
- **Automatic periodic analysis** (configurable interval, default 12h)
- **On-demand MQTT triggers** for immediate analysis
- **REST API endpoints** for dashboard integration
- **Device-specific or bulk analysis** capabilities

## Architecture

### Communication Protocols
- **MQTT Integration**: SenML-formatted data exchange
- **REST API**: Immediate query capabilities
- **Catalog Service**: Auto-discovery and registration
- **InfluxDB Adaptor**: Historical data retrieval

### Data Flow
```
Sensor Data → InfluxDB Adaptor → Energy Optimization → ML Analysis → Recommendations → MQTT/REST Output
```

## Configuration (settings.json)

### Service Information
```json
{
  "serviceInfo": {
    "serviceID": "EnergyOptimization",
    "serviceName": "Energy Optimization Service",
    "serviceDescription": "Analyzes energy consumption patterns using ML prediction",
    "serviceType": "analytics_service",
    "version": "1.0.0",
    "endpoints": [
      "MQTT Publish: Group17/SmartChill/+/Optimization/EnergyReport",
      "MQTT Publish: Group17/SmartChill/+/Optimization/Predictions",
      "MQTT Subscribe: Group17/SmartChill/EnergyOptimization/trigger_analysis",
      "MQTT Subscribe: Group17/SmartChill/EnergyOptimization/config_update"
    ]
  }
}
```

### External Service Configuration
```json
{
  "catalog": {
    "url": "http://catalog:8001",
    "registration_interval_seconds": 300,
    "ping_interval_seconds": 60
  },
  "influxdb_adaptor": {
    "base_url": "http://influxdb_adaptor:8002",
    "timeout_seconds": 15
  },
  "mqtt": {
    "brokerIP": "mosquitto",
    "brokerPort": 1883,
    "clientID_prefix": "EnergyOptimization"
  }
}
```

### Analysis Configuration
```json
{
  "analysis": {
    "automatic_analysis_interval_hours": 12,
    "data_lookback_hours": 168,
    "min_data_points_required": 50,
    "enable_ml_predictions": true,
    "prediction_horizon_days": 7
  }
}
```

**Parameters:**
- `automatic_analysis_interval_hours`: How often to run automatic analysis
- `data_lookback_hours`: Historical data window for analysis (default: 1 week)
- `min_data_points_required`: Minimum sensor readings needed for reliable analysis
- `enable_ml_predictions`: Toggle ML-based forecasting
- `prediction_horizon_days`: Number of days to predict energy consumption

### Machine Learning Configuration
```json
{
  "ml_config": {
    "model_type": "linear_regression",
    "features": [
      "total_door_openings",
      "avg_door_duration",
      "avg_temperature",
      "temperature_variance",
      "hour_of_day_factor",
      "day_of_week_factor",
      "ambient_efficiency_factor"
    ],
    "retrain_interval_hours": 72,
    "min_training_samples": 100
  }
}
```

**Feature Descriptions:**
- `total_door_openings`: Number of door openings in analysis period
- `avg_door_duration`: Average time door remains open
- `avg_temperature`: Mean internal temperature
- `temperature_variance`: Temperature stability measure
- `hour_of_day_factor`: Circadian usage pattern (0-1)
- `day_of_week_factor`: Weekday vs weekend pattern (0-1)
- `ambient_efficiency_factor`: Temperature-based efficiency rating

### Power Estimation Model
```json
{
  "power_estimation": {
    "default_base_power_watts": 150,
    "door_opening_penalty_watts": 25,
    "temperature_variance_penalty": 0.05,
    "compressor_duty_cycle_base": 0.3,
    "efficiency_curve": {
      "optimal_temp_range": [2, 6],
      "penalty_per_degree": 0.02
    }
  }
}
```

**Power Model Formula:**
```
Estimated Power = (Base Power + Door Penalties + Temperature Penalties) / Efficiency Factor
Daily kWh = (Estimated Power × 24) / 1000
```

### Recommendation Engine
```json
{
  "recommendations": {
    "enable_behavioral_tips": true,
    "enable_threshold_alerts": true,
    "savings_threshold_percent": 10,
    "anomaly_detection_sensitivity": 2.0
  }
}
```

### Device-Specific Settings
```json
{
  "devices": {
    "SmartChill_445566": {
      "base_power_watts": 140,
      "target_daily_kwh": 3.5,
      "enable_predictions": true,
      "user_behavior_profile": "efficient"
    },
    "SmartChill_DDEEFF": {
      "base_power_watts": 160,
      "target_daily_kwh": 4.2,
      "enable_predictions": true,
      "user_behavior_profile": "standard"
    }
  }
}
```

**User Behavior Profiles:**
- `efficient`: Lower door opening frequency, optimal temperature settings
- `standard`: Average usage patterns
- `heavy`: Frequent access, higher energy consumption

## API Endpoints

### REST API (Port 8003)

#### GET /health
Health check endpoint
```json
{
  "status": "healthy",
  "service": "Energy Optimization",
  "mqtt_connected": true,
  "known_devices": 2,
  "ml_models_trained": 2
}
```

#### GET /status
Detailed service status
```json
{
  "service_id": "EnergyOptimization",
  "status": "running",
  "mqtt_connected": true,
  "known_devices": 2,
  "trained_models": 2,
  "last_analysis": {
    "SmartChill_445566": "2025-09-04T10:30:00Z"
  },
  "ml_enabled": true,
  "auto_analysis_interval_hours": 12
}
```

#### GET /optimize/{device_id}
Run immediate energy optimization analysis
```json
{
  "device_id": "SmartChill_445566",
  "analysis": {
    "current_energy": {
      "daily_kwh": 3.2,
      "estimated_watts": 133.3,
      "efficiency_factor": 0.95
    },
    "recommendations": [...],
    "predictions": [...]
  },
  "status": "completed"
}
```

#### GET /predictions/{device_id}
Get energy consumption predictions
```json
{
  "device_id": "SmartChill_445566",
  "predictions": [
    {
      "day": 1,
      "date": "2025-09-05",
      "predicted_kwh": 3.1
    }
  ],
  "model_info": {
    "last_trained": "2025-09-04T08:00:00Z",
    "accuracy": {"mae": 0.15, "r2": 0.82}
  }
}
```

### MQTT Topics

#### Subscriptions
- `Group17/SmartChill/EnergyOptimization/trigger_analysis`
- `Group17/SmartChill/EnergyOptimization/config_update`

#### Publications
- `Group17/SmartChill/{device_id}/Optimization/EnergyReport`
- `Group17/SmartChill/{device_id}/Optimization/Predictions`

## MQTT Message Formats

### Analysis Trigger
```json
{
  "type": "trigger_analysis",
  "device_id": "SmartChill_445566"
}
```

### Energy Report (SenML Format)
```json
{
  "bn": "SmartChill_445566/",
  "bt": 1725447600,
  "e": [
    {
      "n": "daily_kwh_estimate",
      "u": "kWh",
      "v": 3.2,
      "t": 0
    },
    {
      "n": "current_power_watts",
      "u": "W", 
      "v": 133.3,
      "t": 0
    },
    {
      "n": "efficiency_factor",
      "v": 0.95,
      "t": 0
    }
  ]
}
```

### Predictions (SenML Format)
```json
{
  "bn": "SmartChill_445566/",
  "bt": 1725447600,
  "e": [
    {
      "n": "prediction_day_1",
      "u": "kWh",
      "v": 3.1,
      "t": 86400
    },
    {
      "n": "prediction_day_2", 
      "u": "kWh",
      "v": 3.3,
      "t": 172800
    }
  ]
}
```

## Installation & Deployment

### Prerequisites
```bash
pip install numpy scikit-learn requests cherrypy
```

### Docker Deployment
```dockerfile
FROM python:3.9-slim
COPY Energy_Optimization.py MyMQTT.py settings.json ./
RUN pip install numpy scikit-learn requests cherrypy
CMD ["python", "Energy_Optimization.py"]
```

### Docker Compose Integration
```yaml
energy-optimization:
  build: ./energy-optimization
  ports:
    - "8003:8003"
  depends_on:
    - mosquitto
    - catalog
    - influxdb-adaptor
  volumes:
    - ./config/energy_settings.json:/settings.json
```

## Usage Examples

### Trigger Analysis via MQTT
```python
import paho.mqtt.client as mqtt

client = mqtt.Client()
client.connect("mosquitto", 1883, 60)

# Analyze specific device
message = {"type": "trigger_analysis", "device_id": "SmartChill_445566"}
client.publish("Group17/SmartChill/EnergyOptimization/trigger_analysis", 
               json.dumps(message))

# Analyze all devices
message = {"type": "trigger_analysis", "device_id": "all"}
client.publish("Group17/SmartChill/EnergyOptimization/trigger_analysis", 
               json.dumps(message))
```

### Query via REST API
```python
import requests

# Get optimization analysis
response = requests.get("http://energy-optimization:8003/optimize/SmartChill_445566")
analysis = response.json()

# Get predictions
response = requests.get("http://energy-optimization:8003/predictions/SmartChill_445566")
predictions = response.json()
```

### Update Device Configuration
```json
{
  "type": "energy_optimization_config_update",
  "device_id": "SmartChill_445566",
  "config": {
    "target_daily_kwh": 3.0,
    "user_behavior_profile": "efficient"
  }
}
```

## Monitoring & Troubleshooting

### Service Logs
The service provides detailed logging for:
- Data retrieval from InfluxDB Adaptor
- ML model training and accuracy
- Analysis execution and results
- MQTT message handling
- API request processing

### Common Issues

**Insufficient Data Error**
```
[ANALYSIS] Insufficient data for SmartChill_445566: 25 points
```
*Solution: Ensure device has been collecting data for sufficient time (default: 50+ data points)*

**Model Training Failure**
```
[ML] Insufficient training data for SmartChill_445566: 45 samples
```
*Solution: Lower `min_training_samples` in config or wait for more historical data*

**InfluxDB Adaptor Connection Error**
```
[DATA] Error connecting to InfluxDB Adaptor: Connection refused
```
*Solution: Verify InfluxDB Adaptor service is running and accessible*

## Performance Considerations

### Scalability
- **Concurrent Analysis**: Service processes devices sequentially to avoid resource contention
- **Memory Usage**: ML models are stored in memory; monitor RAM usage with many devices
- **CPU Usage**: Analysis is CPU-intensive during ML training phases

### Optimization Tips
- Adjust `data_lookback_hours` based on available data and performance requirements
- Increase `automatic_analysis_interval_hours` for large deployments
- Consider disabling ML predictions for devices with minimal usage patterns

## Security & Privacy

### Data Handling
- No persistent storage of sensitive data
- Temporary caching of analysis results only
- All communication via encrypted channels (if MQTT/REST configured with TLS)

### Access Control
- REST API should be behind authentication proxy in production
- MQTT topics should use access control lists (ACLs)
- Configuration updates require proper authorization

## Contributing

### Development Setup
```bash
git clone <repository>
cd energy-optimization
pip install -r requirements.txt
python Energy_Optimization.py
```

### Testing
- Unit tests for ML model accuracy
- Integration tests with mock InfluxDB Adaptor
- End-to-end testing with real sensor data

## License

[License information]

## Support

For technical support and feature requests, please contact the SmartChill development team.