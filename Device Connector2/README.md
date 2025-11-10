# SmartChill Fridge Simulator

The **Fridge Simulator** emulates a SmartChill refrigerator device, producing realistic sensor data and door events in **SenML format**, and publishing them over MQTT.  
It can register with the Catalog Service, send periodic telemetry, and react to user commands.

---

## Features
- Simulates sensors: **temperature, humidity, gas, light**  
- Generates **door events** (open/close with duration)  
- Publishes **heartbeat messages** for device liveness  
- Sends all telemetry in **SenML format** over MQTT  
- Supports **manual user commands** (open/close door, spoilage, malfunction, etc.)  
- Automatically registers with the **Catalog Service** and retrieves assigned topics  

---

## MQTT Topics

Topics follow this template (from `settings.json`):  
```
Group17/SmartChill/Devices/{model}/{device_id}/{sensor}
Group17/SmartChill/Devices/{model}/{device_id}/door_event
Group17/SmartChill/Devices/{model}/{device_id}/heartbeat
```

Example sensor publish (temperature):  
```json
{
  "bn": "SmartChill_A4B2C3D91E7F/",
  "bt": 1694250000.0,
  "e": [{
    "n": "temperature",
    "v": 4.2,
    "u": "Cel",
    "t": 0
  }]
}
```

Example door event publish (closed):  
```json
{
  "bn": "SmartChill_A4B2C3D91E7F/",
  "bt": 1694250100.0,
  "e": [
    { "n": "door_state", "vs": "door_closed", "t": 0 },
    { "n": "door_duration", "v": 35.2, "u": "s", "t": 0 }
  ]
}
```

Example heartbeat publish:  
```json
{
  "bn": "SmartChill_A4B2C3D91E7F/",
  "bt": 1694250200.0,
  "e": [
    { "n": "heartbeat", "vs": "alive", "t": 0 },
    { "n": "uptime", "v": 123456.7, "u": "s", "t": 0 }
  ]
}
```

---

## Configuration

Defined in **`settings.json`**:
- **Catalog URL**: `http://192.168.1.184:8001`  
- **Device Info**: MAC `A4:B2:C3:D9:1E:7F`, model `Samsung_RF28T5001SR`, firmware `1.2.3`  
- **Sensors**: temperature, humidity, gas, light  
- **Sampling Intervals**: temperature/humidity/gas = 600s, light = 20s  
- **MQTT Broker**: `localhost:1884`  
- **Telemetry**: QoS 2, retain disabled, heartbeat every 300s  

---

## User Commands

- `apri` → Open fridge door  
- `chiudi` → Close fridge door  
- `spoilage` → Simulate food spoilage (gas sensor spikes)  
- `malfunzione` → Simulate malfunction (temperature rises uncontrollably)  
- `normale` → Return to normal operation  
- `status` → Print simulator status  
- `help` → Show available commands  
- `quit` / `exit` → Stop simulator  

---

## Run
```bash
python Fridge.py
```

The simulator will:  
1. Register the device with the Catalog (if available)  
2. Connect to the MQTT broker  
3. Start publishing sensor data, door events, and heartbeat messages  
4. Accept user commands via console
