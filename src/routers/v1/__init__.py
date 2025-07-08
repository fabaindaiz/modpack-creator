from fastapi import FastAPI
import src.routers.v1.auth as auth
import src.routers.v1.project as project
import src.routers.v1.server as server
import src.routers.v1.users as users

MODULES_V1 = [
    auth,
    project,
    server,
    users,
]

ROUTE_PREFIX = "/v1"
def include_routers_v1(app: FastAPI) -> None:
    app.include_router(auth.router, prefix=ROUTE_PREFIX, include_in_schema=False)
    app.include_router(project.router, prefix=ROUTE_PREFIX)
    app.include_router(server.router, prefix=ROUTE_PREFIX)
    app.include_router(users.router, prefix=ROUTE_PREFIX)

tags_metadata = [
    {
        "name": "project",
        "description": "Project endpoint. Query data and manage projects.",
    },
    {
        "name": "server",
        "description": "Server endpoint. Query data and manage servers.",
    },
    {
        "name": "users",
        "description": "User endpoint. Query user data.",
    }
]