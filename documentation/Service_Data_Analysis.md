# SmartChill Data Analysis Service Documentation

## 1. Core Purpose
The **Data Analysis Service** is the "statistician" of the SmartChill platform. While other services focus on real-time control (alerts), this service focuses on **historical insight**. Its responsibilities are:
-   **Aggregating Data**: It fetches raw sensor data and event logs from the *InfluxDB Adaptor*.
-   **Computing Metrics**: It calculates high-level metrics like "Temperature Stability Score", "Door Usage Efficiency", and "Out-of-Range Percentage".
-   **Trend Detection**: It analyzes data over time to detect if the fridge is slowly warming up or if usage is increasing, which might predict future failures.
-   **Serving the Frontend**: It provides these processed insights to the Dashboard (Node-RED) via a REST API, so the user sees graphs and scores instead of raw numbers.

## 2. Code Explanation and Justification

### Class Structure: `DataAnalysisService`
The service is structured around a central class that manages data fetching and processing.

#### Data Fetching (`fetch_sensor_data_from_adaptor`)
*   **Decoupling**: The service does *not* connect to InfluxDB directly. Instead, it queries the *InfluxDB Adaptor*.
*   **Justification**: This enforces the microservice pattern. If we change the database technology (e.g., to SQL or Prometheus), we only need to update the Adaptor, not the Analysis service.

#### Metric Calculation (`analyze_temperature_data`)
*   **Stability Score**: Instead of just average temperature, we calculate the standard deviation (`np.std`).
    *   *Logic*: A fridge that fluctuates between 2°C and 8°C is worse than one steady at 5°C, even if the average is the same. The code maps standard deviation to a 0-100 score.
*   **Out-of-Range %**: Calculates the percentage of time the temperature was outside the safe zone (2-6°C). This is a critical food safety metric.

#### Usage Analysis (`analyze_door_usage`)
*   **Efficiency Score**: Penalizes the user for:
    *   Too many openings (>15/day).
    *   Long duration openings (>60s).
    *   Leaving the door open (>3 mins).
*   **Justification**: This gamifies the user experience, encouraging better habits that save energy.

#### Trend Analysis (`analyze_trends`)
*   **Linear Regression**: Uses `np.polyfit` to find the slope of the temperature curve over the last week.
*   **Insight**: A positive slope indicates the fridge is struggling to maintain temperature, potentially signaling a coolant leak or dust buildup on the coils *before* it fails completely.

## 3. Configuration (`settings.json`)

### Key Sections:
*   **`influxdb_adaptor`**: Defines the URL of the data source.
*   **`analysis`**:
    *   **`supported_periods`**: Defines valid time ranges (e.g., "1d", "7d") to prevent invalid queries.
    *   **`temperature_optimal_range`**: [2, 6]. This is configurable so it can be adjusted for different types of appliances (e.g., a freezer would be [-20, -15]).
*   **`serviceInfo`**: Standard registration block for the Catalog.

### Why this structure?
By keeping analysis parameters (like optimal ranges and thresholds) in `settings.json`, we can tune the "strictness" of the analysis without redeploying the code.
