# SmartChill Energy Optimization Service

This service uses Machine Learning to analyze power consumption and usage patterns, providing personalized recommendations to save energy.

## 📂 Folder Architecture

* **`main.py`**: Entry point.
* **`optimizer_service.py`**: Manages data fetching, model persistence, and API endpoints.
* **`optimizer_utils.py`**: Contains Scikit-learn models, training logic, and compressor cycle analysis.

## ⚙️ Functionality

* **Runtime Prediction**: Uses Linear Regression to predict future compressor runtime based on usage history.
* **Cycle Analysis**: Estimates the fridge's duty cycle (ON/OFF ratio).
* **Recommendations**: Generates actionable advice (e.g., "Check door seals") based on efficiency analysis.

## 📡 Interfaces

### REST APIs
* `GET /optimize/{device_id}`: Returns a full optimization report containing estimates and recommendations.
* `GET /predictions/{device_id}`: Returns ML-based runtime predictions for the next 7 days.
* `GET /runtime/{device_id}`: Returns detailed cycle analysis.

### Data Format
* **Output**: JSON containing kWh estimates, savings potential, and prediction arrays.