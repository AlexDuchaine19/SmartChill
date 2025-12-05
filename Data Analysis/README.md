# SmartChill Data Analysis Service

The **Data Analysis Service** processes historical data retrieved from InfluxDB to provide insights into fridge usage and performance.

## 📂 Folder Architecture

* **`main.py`**: Entry point. Starts the service and handles lifecycle.
* **`data_analysis_service.py`**: Service layer handling REST APIs, Catalog registration, and Networking.
* **`analysis_logic.py`**: Pure logic for statistical calculations (NumPy) and trend analysis.
* **`settings.json`**: Configuration file.

## ⚙️ Functionality

* **Temperature Analysis**: Calculates stability scores, variance, and out-of-range percentages.
* **Usage Patterns**: Analyzes door opening frequency and duration.
* **Trend Detection**: Identifies long-term trends (e.g., "cooling efficiency decreasing").

## 📡 Interfaces

### REST APIs
* `GET /analyze/{device_id}`: returns a full analysis report.
    * *Params:* `period` (e.g., "7d"), `metrics` (e.g., "temperature,usage").
* `GET /trends/{device_id}`: Returns specific trend data (increasing/decreasing/stable).
* `GET /health`: Service health check.

### Data Format
* **Input**: JSON data fetched from InfluxDB Adaptor.
* **Output**: JSON reports containing statistical metrics and scores.
