import cherrypy
import time

class EnergyOptimizationRestAPI:
    exposed = True

    def __init__(self, service):
        self.service = service

    @cherrypy.tools.json_out()
    def GET(self, *path, **args):
        if not path:
            return self.service.get_status()
        
        if path[0] == "analyze":
            device_id = args.get("device_id")
            period = args.get("period", "7d")
            if not device_id:
                raise cherrypy.HTTPError(400, "Missing device_id")
            return self.service.analyze_device_energy(device_id, period)
            
        raise cherrypy.HTTPError(404)
