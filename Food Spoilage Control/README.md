# SmartChill Food Spoilage Control Service

The **Food Spoilage Control Service** monitors fridge gas sensor data via MQTT and triggers alerts if gas levels exceed configured thresholds, indicating potential food spoilage.  
It auto-registers with the Catalog Service, maintains device configurations, and supports runtime updates.

---

## Features
- Subscribes to **gas sensor MQTT topics** for all SmartChill devices  
- Detects gas concentration exceeding configured **thresholds (ppm)**  
- Publishes **spoilage alerts** to MQTT with severity and recommendations  
- Supports **per-device configuration** with defaults and overrides  
- Handles **config updates** dynamically via MQTT  
- Auto-registers devices if discovered but not configured  

---

## MQTT Topics

**Subscriptions**
- `Group17/SmartChill/Devices/+/+/gas` – Receive gas sensor data in SenML format  
- `Group17/SmartChill/FoodSpoilageControl/config_update` – Receive config updates  

**Publications**
- `Group17/SmartChill/{device_id}/Alerts/Spoilage` – Spoilage alerts  
- `Group17/SmartChill/FoodSpoilageControl/config_ack` – Config update acknowledgements  

---

## Example Messages

**Incoming Gas Sensor Data (SenML)**  
```json
{
  "bn": "SmartChill_A4B2C3D91E7F/",
  "bt": 1694250000.0,
  "e": [
    { "n": "gas", "v": 420.5, "u": "ppm", "t": 0 }
  ]
}
```

**Spoilage Alert (MQTT Publish)**  
```json
{
  "alert_type": "food_spoilage",
  "device_id": "SmartChill_A4B2C3D91E7F",
  "message": "High gas levels detected: 420.5 PPM (threshold: 300 PPM). Possible food spoilage.",
  "gas_level_ppm": 420.5,
  "threshold_ppm": 300,
  "over_threshold_by": 120.5,
  "severity": "warning",
  "timestamp": "2025-09-09T14:35:00Z",
  "service": "FoodSpoilageControl",
  "config_version": 4,
  "recommended_action": "Check fridge contents for spoiled food"
}
```

**Config Update Acknowledgement**  
```json
{
  "device_id": "SmartChill_A4B2C3D91E7F",
  "status": "updated",
  "timestamp": "2025-09-09T14:36:00Z",
  "config_version": 5
}
```

---

## Configuration

Defined in **`settings.json`**:
- **Catalog URL**: `http://catalog:8001`  
- **MQTT Broker**: `mosquitto:1883`  
- **Defaults**:  
  - Gas threshold: `300 ppm`  
  - Alert severity: `warning`  
  - Continuous alerts: disabled  
  - Alert cooldown: `15 minutes`  

Example per-device config:
```json
{
  "SmartChill_A4B2C3D91E7F": {
    "gas_threshold_ppm": 300,
    "enable_continuous_alerts": false,
    "alert_cooldown_minutes": 15
  }
}
```

---

## Run
```bash
python SpoilageControl.py
```

The service will:  
1. Register with the Catalog  
2. Load known devices  
3. Connect to MQTT broker  
4. Subscribe to gas topics and listen for config updates  
5. Publish alerts when thresholds are exceeded
