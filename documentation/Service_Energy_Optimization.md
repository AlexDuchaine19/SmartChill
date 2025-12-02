# SmartChill Energy Optimization Service Documentation

## 1. Core Purpose
The **Energy Optimization Service** is the most advanced component of the platform, leveraging **Machine Learning** to reduce energy waste. Its goals are:
-   **Analyze Compressor Cycles**: It detects when the fridge compressor turns ON and OFF by analyzing the "sawtooth" temperature pattern.
-   **Predict Energy Usage**: It uses a Linear Regression model to predict future energy consumption based on usage habits (door openings) and environmental factors.
-   **Generate Recommendations**: It provides actionable advice to the user (e.g., "Reduce door openings by 2/day to save 0.5 kWh").

## 2. Code Explanation and Justification

### Class Structure: `EnergyOptimizationService`
This service is a hybrid: it acts as both a REST API server and a background ML worker.

#### Compressor Cycle Analysis (`analyze_compressor_cycles`)
*   **The Problem**: We don't have a "compressor status" sensor. We only have temperature.
*   **The Solution**: The code implements a signal processing algorithm that looks for rapid temperature drops (cooling phase) vs. slow rises (warming phase).
*   **Duty Cycle**: It calculates the ratio of ON time to Total time. A high duty cycle (>60%) indicates the fridge is working too hard, possibly due to a seal leak or hot food.

#### Machine Learning (`train_runtime_model`)
*   **Algorithm**: **Linear Regression** (Scikit-learn).
*   **Features**:
    *   `avg_temperature`: Is the fridge set too cold?
    *   `daily_openings`: How often is the door opened?
    *   `avg_door_duration`: How long does it stay open?
    *   `day_of_week`: Is it a weekend? (People use fridges more on weekends).
*   **Target**: `runtime_hours` (How long the compressor ran that day).
*   **Justification**: Linear Regression is chosen for its interpretability. We can easily see *why* the model predicts high energy usage (e.g., "It's because you opened the door 50 times"). It's also lightweight enough to run on a Raspberry Pi.

#### Personalized Models
*   **Dictionary Storage**: `self.ml_models = {}`.
*   **Logic**: The service trains a *separate* model for each fridge (`device_id`).
*   **Justification**: A large family fridge behaves differently from a dorm mini-fridge. A single global model would be inaccurate. Personalized models adapt to the specific thermal characteristics and user habits of each device.

## 3. Configuration (`settings.json`)

### Key Sections:
*   **`ml`**:
    *   **`enable_predictions`**: Master switch for ML features.
    *   **`min_training_samples`**: Minimum days of data required before training starts (set to 3 for demo purposes, usually 7-14).
*   **`defaults`**:
    *   **`fallback_power_specs`**: Default wattage (120W) used if the specific fridge model isn't found in the Catalog.

### Why this structure?
The `ml` section allows us to disable expensive training operations on low-power hardware without changing code. The `fallback_power_specs` ensures the service is robust and doesn't crash if a new, unknown device connects.
