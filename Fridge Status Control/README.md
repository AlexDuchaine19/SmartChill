# SmartChill Fridge Status Control Service

The **Fridge Status Control Service** monitors temperature and humidity data from SmartChill refrigerators via MQTT.  
It detects abnormal conditions (too hot, too cold, high humidity) and publishes malfunction alerts.  
The service also auto-registers with the Catalog, keeps per-device configurations, and supports live config updates.

---

## Features
- Subscribes to **temperature** and **humidity** sensor topics (SenML format)  
- Detects:  
  - Temperature too high → risk of spoilage  
  - Temperature too low → risk of freezing  
  - Humidity too high → risk of condensation/ice  
  - Combined abnormal patterns → possible cooling/defrost malfunctions  
- Publishes **malfunction alerts** with recommended actions  
- Supports **per-device config overrides** (thresholds, cooldowns)  
- Handles **config updates** dynamically via MQTT  
- Auto-registers unknown devices with default settings  

---

## MQTT Topics

**Subscriptions**
- `Group17/SmartChill/Devices/+/+/temperature` – Temperature data (SenML)  
- `Group17/SmartChill/Devices/+/+/humidity` – Humidity data (SenML)  
- `Group17/SmartChill/FridgeStatusControl/config_update` – Config update messages  

**Publications**
- `Group17/SmartChill/{device_id}/Alerts/Malfunction` – Malfunction alerts  
- `Group17/SmartChill/FridgeStatusControl/config_ack` – Config update acknowledgements  

---

## Example Messages

**Incoming Temperature Data (SenML)**  
```json
{
  "bn": "SmartChill_A4B2C3D91E7F/",
  "bt": 1694251000.0,
  "e": [
    { "n": "temperature", "v": 9.3, "u": "Cel", "t": 0 }
  ]
}
```

**Malfunction Alert (Temperature Too High)**  
```json
{
  "alert_type": "temperature_too_high",
  "device_id": "SmartChill_A4B2C3D91E7F",
  "message": "Temperature too high: 9.3°C (max: 8.0°C). Risk of food spoilage.",
  "sensor_values": { "temperature": 9.3 },
  "severity": "critical",
  "timestamp": "2025-09-09T14:50:00Z",
  "service": "FridgeStatusControl",
  "config_version": 2,
  "recommended_action": "Check thermostat settings, door seals, and reduce temperature"
}
```

**Config Update Acknowledgement**  
```json
{
  "device_id": "SmartChill_A4B2C3D91E7F",
  "status": "updated",
  "timestamp": "2025-09-09T14:52:00Z",
  "config_version": 3
}
```

---

## Configuration

Defined in **`settings.json`**:
- **Catalog URL**: `http://catalog:8001`  
- **MQTT Broker**: `mosquitto:1883`  
- **Defaults**:  
  - Temperature range: `0.0–8.0 °C`  
  - Max humidity: `85%`  
  - Malfunction alerts: enabled  
  - Alert cooldown: `30 minutes`  

Example per-device config:
```json
{
  "SmartChill_A4B2C3D91E7F": {
    "temp_min_celsius": 0.0,
    "temp_max_celsius": 8.0,
    "humidity_max_percent": 85.0,
    "enable_malfunction_alerts": true,
    "alert_cooldown_minutes": 30
  }
}
```

---

## Run
```bash
python StatusControl.py
```

The service will:  
1. Register with the Catalog  
2. Load known devices  
3. Connect to the MQTT broker  
4. Monitor temperature and humidity data  
5. Publish malfunction alerts if thresholds are exceeded
