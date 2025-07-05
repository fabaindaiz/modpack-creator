from fastapi import FastAPI
import src.routers.v1.server as server
import src.routers.v1.token as token

MODULES_V1 = [
    server,
    token
]

ROUTE_PREFIX = "/v1"
def include_routers_v1(app: FastAPI) -> None:
    app.include_router(server.router, prefix=ROUTE_PREFIX)
    app.include_router(token.router, prefix=ROUTE_PREFIX)

tags_metadata = [
    {
        "name": "server",
        "description": "Server endpoint. Query data and execute actions.",
    },
    {
        "name": "token",
        "description": "Authentication endpoint.",
    }
]