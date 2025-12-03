# SmartChill Energy Optimization Service

The **Energy Optimization Service** analyzes device consumption patterns, estimates energy usage, and generates **recommendations and predictions** for SmartChill refrigerators.  
It integrates with the **Catalog Service** and the **Data Analysis Service**, and uses ML models for prediction.

---

## Endpoints & Example Responses

### Health & Status

**GET /health** – Service health check  
```json
{
  "status": "healthy",
  "service": "Energy Optimization",
  "mode": "REST-only",
  "timestamp": "2025-09-09T14:20:00Z",
  "known_devices": 3,
  "ml_models": 2
}
```

**GET /status** – Detailed service status  
```json
{
  "service_id": "Energy Optimization Service",
  "status": "running",
  "known_devices": 3,
  "trained_models": 2,
  "data_analysis_service": "http://data_analysis:8004",
  "ml_enabled": true,
  "config_version": 1
}
```

---

### Optimization

**GET /optimize/{device_id}** – Perform energy analysis for a device  
```json
{
  "device_id": "SmartChill_A4B2C3D91E7F",
  "model": "Samsung_RF28T5001SR",
  "analysis_timestamp": "2025-09-09T14:22:00Z",
  "data_source": "data_analysis_service",
  "current_energy": {
    "estimated_watts": 145.3,
    "daily_kwh": 3.48,
    "base_power": 120,
    "door_penalty": 12.5,
    "temp_penalty": 4.8,
    "efficiency_factor": 0.92,
    "trend_factor": 1.05
  },
  "ml_features": {
    "avg_temperature": 4.1,
    "door_openings_per_day": 6,
    "avg_door_duration": 40.2
  },
  "recommendations": [
    {
      "type": "behavioral",
      "priority": "high",
      "message": "Reduce door openings: 18.0/day. Target: <12/day",
      "potential_savings_kwh": 0.5,
      "potential_savings_percent": 14.3
    },
    {
      "type": "setting",
      "priority": "medium",
      "message": "Temperature too low: 1.8°C. Raise to 2.0°C",
      "potential_savings_kwh": 0.4,
      "potential_savings_percent": 10
    }
  ],
  "service": "Energy Optimization Service"
}
```

---

### Predictions

**GET /predictions/{device_id}** – ML-based energy consumption forecasts  
```json
{
  "device_id": "SmartChill_A4B2C3D91E7F",
  "model": "Samsung_RF28T5001SR",
  "predictions": [
    { "day": 1, "date": "2025-09-10", "predicted_kwh": 3.45, "timestamp": 1757410800 },
    { "day": 2, "date": "2025-09-11", "predicted_kwh": 3.42, "timestamp": 1757497200 }
  ],
  "model_info": {
    "last_trained": "2025-09-09T14:18:00Z",
    "accuracy": { "mae": 0.123, "r2": 0.89 }
  },
  "timestamp": "2025-09-09T14:25:00Z"
}
```

---

### Devices

**GET /devices** – List all known devices with specs  
```json
{
  "devices": [
    {
      "device_id": "SmartChill_A4B2C3D91E7F",
      "model": "Samsung_RF28T5001SR",
      "power_specs": {
        "base_power_watts": 120,
        "door_penalty_watts": 30,
        "temp_variance_penalty": 0.12,
        "optimal_temp_range": [2.0,5.0],
        "temp_penalty_per_degree": 0.04,
        "max_efficient_openings_per_day": 12
      },
      "ml_model_trained": true
    }
  ],
  "total_devices": 1,
  "timestamp": "2025-09-09T14:27:00Z"
}
```

---

### Models

**GET /models** – List all fridge models with power specs  
```json
{
  "models": {
    "Samsung_RF28T5001SR": {
      "base_power_watts": 120,
      "door_penalty_watts": 30,
      "temp_variance_penalty": 0.12,
      "optimal_temp_range": [2.0,5.0],
      "temp_penalty_per_degree": 0.04,
      "max_efficient_openings_per_day": 12
    }
  },
  "timestamp": "2025-09-09T14:28:00Z"
}
```

---

## Configuration

Defined in **`settings.json`**:
- **Catalog URL**: `http://catalog:8001`  
- **Data Analysis Service**: `http://data_analysis:8004`  
- **Machine Learning**: enabled, features for training defined in settings  
- **Fallback power specs**: default values when catalog data unavailable  

---

## Run
```bash
python Optimizer.py
# Service runs at http://localhost:8003
```

## Modular Architecture

This service has been refactored into a modular architecture to improve maintainability and scalability.

- **`modules/utils.py`**: Helper functions for settings management and common utilities.
- **`modules/catalog_client.py`**: Handles interactions with the Catalog service (registration, device lookup).
- **`modules/data_analysis_client.py`**: Client for interacting with the Data Analysis service.
- **`modules/analyzer.py`**: Contains the core logic for energy analysis and recommendations.
- **`modules/predictor.py`**: Manages ML models for energy prediction.
- **`Optimizer.py`**: The entry point that initializes and orchestrates the modules.
