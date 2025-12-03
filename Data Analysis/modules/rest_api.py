import cherrypy
from datetime import datetime, timezone

class DataAnalysisRestAPI:
    """REST API endpoints for Data Analysis Service"""
    
    def __init__(self, service):
        self.service = service
    
    @cherrypy.expose
    @cherrypy.tools.json_out()
    def health(self):
        """GET /health - Health check endpoint"""
        try:
            return {
                "status": "healthy",
                "service": "Data Analysis",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "known_devices": len(self.service.known_devices)
            }
        except Exception as e:
            cherrypy.response.status = 500
            return {
                "status": "unhealthy",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
    
    @cherrypy.expose
    @cherrypy.tools.json_out()
    def status(self):
        """GET /status - Detailed service status"""
        return self.service.get_status()
    
    @cherrypy.expose
    @cherrypy.tools.json_out()
    def analyze(self, device_id, **params):
        """GET /analyze/{device_id}?period={duration}&metrics={list}"""
        try:
            # Check if device exists
            if device_id not in self.service.known_devices:
                if not self.service.catalog_client.check_device_exists(device_id):
                    cherrypy.response.status = 404
                    return {
                        "error": f"Device {device_id} not found in catalog",
                        "known_devices": list(self.service.known_devices)
                    }
            
            # Extract parameters
            period = params.get("period", self.service.settings["defaults"]["default_period"])
            metrics = params.get("metrics", "temperature,usage_patterns,trends")
            
            # Validate period
            if not self.service.validate_period(period):
                cherrypy.response.status = 400
                return {
                    "error": f"Unsupported period: {period}",
                    "supported_periods": self.service.settings["analysis"]["supported_periods"]
                }
            
            # Convert metrics to list
            metrics_list = metrics.split(",") if isinstance(metrics, str) else metrics
            
            # Perform analysis (no cache)
            result = self.service.perform_full_analysis(device_id, period, metrics_list)
            
            return result
            
        except ValueError as e:
            cherrypy.response.status = 400
            return {"error": str(e)}
        except Exception as e:
            print(f"[REST] Error in analyze endpoint: {e}")
            cherrypy.response.status = 500
            return {
                "error": "Internal server error",
                "details": str(e)
            }
    
    @cherrypy.expose
    @cherrypy.tools.json_out()
    def trends(self, device_id, **params):
        """GET /trends/{device_id}?period={duration}"""
        try:
            # Check if device exists
            if device_id not in self.service.known_devices:
                if not self.service.catalog_client.check_device_exists(device_id):
                    cherrypy.response.status = 404
                    return {"error": f"Device {device_id} not found"}
            
            period = params.get("period", "7d")
            
            if not self.service.validate_period(period):
                cherrypy.response.status = 400
                return {"error": f"Unsupported period: {period}"}
            
            # Fetch data
            temp_data = self.service.influx_client.fetch_sensor_data(device_id, "temperature", period)
            door_events = self.service.influx_client.fetch_door_events(device_id, period)
            
            # Analyze trends
            trends = self.service.analyzer.analyze_trends(temp_data, door_events, period)
            
            return {
                "device_id": device_id,
                "period": period,
                "trends": trends,
                "generated_at": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            print(f"[REST] Error in trends endpoint: {e}")
            cherrypy.response.status = 500
            return {"error": "Internal server error"}
    
    @cherrypy.expose
    @cherrypy.tools.json_out()
    def patterns(self, device_id, **params):
        """GET /patterns/{device_id}?type={usage|temperature|efficiency}"""
        try:
            # Check if device exists
            if device_id not in self.service.known_devices:
                if not self.service.catalog_client.check_device_exists(device_id):
                    cherrypy.response.status = 404
                    return {"error": f"Device {device_id} not found"}
            
            pattern_type = params.get("type", "usage")
            period = params.get("period", "7d")
            
            if pattern_type not in ["usage", "temperature", "efficiency"]:
                cherrypy.response.status = 400
                return {"error": "Invalid pattern type. Must be: usage, temperature, or efficiency"}
            
            if not self.service.validate_period(period):
                cherrypy.response.status = 400
                return {"error": f"Unsupported period: {period}"}
            
            result = {"device_id": device_id, "type": pattern_type, "period": period}
            
            if pattern_type == "usage":
                door_events = self.service.influx_client.fetch_door_events(device_id, period)
                result["patterns"] = self.service.analyzer.analyze_door_usage(door_events, period)
                
            elif pattern_type == "temperature":
                temp_data = self.service.influx_client.fetch_sensor_data(device_id, "temperature", period)
                result["patterns"] = self.service.analyzer.analyze_temperature(temp_data, period)
                
            elif pattern_type == "efficiency":
                temp_data = self.service.influx_client.fetch_sensor_data(device_id, "temperature", period)
                door_events = self.service.influx_client.fetch_door_events(device_id, period)
                
                temp_analysis = self.service.analyzer.analyze_temperature(temp_data, period)
                usage_analysis = self.service.analyzer.analyze_door_usage(door_events, period)
                
                combined_efficiency = (temp_analysis["stability_score"] + usage_analysis["efficiency_score"]) / 2
                
                result["patterns"] = {
                    "overall_efficiency": round(combined_efficiency, 1),
                    "temperature_efficiency": temp_analysis["stability_score"],
                    "usage_efficiency": usage_analysis["efficiency_score"],
                    "factors": {
                        "temperature_stability": temp_analysis["stability_score"] > 80,
                        "optimal_door_usage": usage_analysis["efficiency_score"] > 70,
                        "minimal_out_of_range": temp_analysis["out_of_range_time_percent"] < 10
                    }
                }
            
            result["generated_at"] = datetime.now(timezone.utc).isoformat()
            return result
            
        except Exception as e:
            print(f"[REST] Error in patterns endpoint: {e}")
            cherrypy.response.status = 500
            return {"error": "Internal server error"}
