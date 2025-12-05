# SmartChill – Food Spoilage Control Service

A modular Python service for monitoring gas levels from SmartChill devices, sending spoilage alerts, and managing device configurations via MQTT and a Catalog REST API.

## Overview
The service receives gas sensor data (SenML), detects possible food spoilage, sends alerts, and exposes a full configuration protocol over MQTT.  
All logic is split across dedicated modules for clarity and maintainability.

## Architecture
- **SpoilageControl.py**  
  Main orchestrator. Loads settings, starts the service, routes MQTT messages, parses SenML, and coordinates all modules.

- **modules/config_manager.py**  
  Handles the entire MQTT configuration protocol:  
  - `config_get`, `device_config_update`, `default_config_update`  
  - validation of config values  
  - access control (admin vs device)  
  - sending config_data, config_ack, config_error responses  
  - auto-registration of new devices

- **modules/mqtt_client.py**  
  Manages the MQTT connection using MyMQTT:  
  subscribes to topics, forwards messages to the service, publishes JSON payloads.

- **modules/spoilage_monitor.py**  
  Processes gas readings:  
  compares values to thresholds, handles cooldowns, updates status, publishes spoilage alerts.

- **modules/catalog_client.py**  
  Communicates with the Catalog Service:  
  service registration, device existence checks, loading known devices.

- **modules/utils.py**  
  Loads and saves settings.json with versioning and timestamps.

- **MyMQTT.py**  
  Wrapper around Paho MQTT used by mqtt_client.

## Workflow Summary
1. Devices publish SenML gas data → MQTT  
2. MQTTClient forwards the message → SpoilageControl  
3. SpoilageControl parses SenML → SpoilageMonitor  
4. SpoilageMonitor updates status and may send an alert  
5. Configuration messages (`* /config_update`) → ConfigManager  
6. ConfigManager validates, updates settings, and sends responses

## Running the Service
