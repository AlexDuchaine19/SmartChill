import cherrypy

# DB in memoria
USERS_DB = {}

class UsersController(object):
    @cherrypy.tools.json_in()
    @cherrypy.tools.json_out()
    def create(self):
        """
        POST /users -> 201 se creato, 409 se già esiste
        """
        data = cherrypy.request.json or {}
        user_id = str(data.get("userID", "")).strip()
        user_name = str(data.get("userName", "")).strip()

        if not user_id or not user_name:
            raise cherrypy.HTTPError(400, "userID and userName are required")

        if user_id in USERS_DB:
            raise cherrypy.HTTPError(409, "User already exists")

        USERS_DB[user_id] = {"userID": user_id, "userName": user_name}
        cherrypy.response.status = 201
        return USERS_DB[user_id]

    @cherrypy.tools.json_out()
    def get(self, user_id):
        """
        GET /users/{user_id} -> 200 se trovato, 404 altrimenti
        """
        uid = str(user_id)
        if uid not in USERS_DB:
            raise cherrypy.HTTPError(404, "User not found")
        return USERS_DB[uid]


if __name__ == "__main__":
    # Dispatcher con rotte esplicite
    d = cherrypy.dispatch.RoutesDispatcher()

    users_controller = UsersController()
    d.connect(
        name="users_post",
        route="/users",
        controller=users_controller,
        action="create",
        conditions={"method": ["POST"]},
    )
    d.connect(
        name="users_get",
        route="/users/{user_id}",
        controller=users_controller,
        action="get",
        conditions={"method": ["GET"]},
    )

    conf = {
        "/": {
            "request.dispatch": d,
            "tools.response_headers.on": True,
            "tools.response_headers.headers": [("Content-Type", "application/json")],
        }
    }

    cherrypy.config.update({
        "server.socket_host": "127.0.0.1",
        "server.socket_port": 8001,  # <-- stessa porta che usa il tuo bot
        "log.screen": True,
    })

    cherrypy.quickstart(None, "/", conf)
