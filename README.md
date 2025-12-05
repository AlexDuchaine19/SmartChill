# SmartChill - IoT Fridge Monitoring Platform 🧊

**Version:** 1.0.0
**Team:** Alexander Duchaine (346096), Luca Marchese (261683)
**Course:** Programming for IoT (Politecnico di Torino)

# ❄️ SmartChill - Intelligent IoT Fridge Management System

**SmartChill** is a comprehensive IoT ecosystem designed to monitor, control, and optimize smart refrigerators. Built on a microservices architecture, it leverages MQTT for real-time communication, InfluxDB for time-series data storage, and Machine Learning for energy optimization.

The system provides real-time alerts via Telegram, monitors food spoilage, ensures door security, and predicts energy consumption patterns.

---

## 🏗️ System Architecture

The project follows a **Service-Oriented Architecture (SOA)**. The components interact via two main communication protocols:
1.  **MQTT (Pub/Sub):** Used for real-time telemetry (sensors), events (door opening), and alerts.
2.  **REST API (HTTP):** Used for service discovery (Catalog), configuration, and retrieving historical data.

### High-Level Data Flow

```mermaid
graph TD
    User((User/Telegram)) <--> Bot[Telegram Bot]
    Bot <--> Catalog[Catalog Service]
    
    subgraph "Device Layer"
        Fridge[Fridge Simulator] -- SenML (MQTT) --> Broker((MQTT Broker))
    end
    
    subgraph "Control Layer"
        Broker --> Spoilage[Spoilage Control]
        Broker --> Timer[Timer Control]
        Broker --> Status[Status Control]
        Spoilage -- Alerts (MQTT) --> Broker
        Timer -- Alerts (MQTT) --> Broker
        Status -- Alerts (MQTT) --> Broker
    end
    
    subgraph "Data Layer"
        Broker --> Adaptor[InfluxDB Adaptor]
        Adaptor --> DB[(InfluxDB)]
    end
    
    subgraph "Intelligence Layer"
        Analysis[Data Analysis] -- HTTP --> Adaptor
        Optimizer[Energy Optimizer] -- HTTP --> Adaptor
    end
    
    Bot <-- HTTP --> Analysis
    Bot <-- HTTP --> Optimizer

### Stopping Services

```bash
docker-compose down
```
