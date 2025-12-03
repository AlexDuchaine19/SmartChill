import cherrypy

class InfluxRestAPI:
    exposed = True
    
    def __init__(self, influx_client):
        self.influx_client = influx_client
        
    @cherrypy.tools.json_out()
    def GET(self, *args, **kwargs):
        # Handle /health
        if len(args) == 1 and args[0] == "health":
            return {"status": "healthy", "service": "InfluxDB Adaptor"}
            
        # Handle /status
        if len(args) == 1 and args[0] == "status":
            # This would need access to the main adaptor status, 
            # but for now we can return basic info or pass a status callback
            return {"status": "active"}
            
        # Handle /events
        if len(args) == 1 and args[0] == "events":
            device_filter = kwargs.get("device")
            duration = kwargs.get("duration", "168h")
            limit = kwargs.get("limit")
            
            if limit:
                try:
                    limit = int(limit)
                except ValueError:
                    limit = None
            
            return self.influx_client.query_door_events(device_filter, duration, limit)
            
        # Handle /sensors/{sensor_type}
        if len(args) == 2 and args[0] == "sensors":
            sensor_type = args[1]
            device_filter = kwargs.get("device")
            duration = kwargs.get("duration", "24h")
            last = kwargs.get("last", "false").lower() == "true"
            limit = kwargs.get("limit")
            
            if limit:
                try:
                    limit = int(limit)
                except ValueError:
                    limit = None
            
            return self.influx_client.query_sensor_data(sensor_type, device_filter, duration, last, limit)
            
        raise cherrypy.HTTPError(404, "Endpoint not found")
