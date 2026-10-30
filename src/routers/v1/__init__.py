from fastapi import FastAPI
import src.routers.v1.auth as auth
import src.routers.v1.jobs as jobs
import src.routers.v1.knowledge as knowledge
import src.routers.v1.modpack as modpack
import src.routers.v1.modrinth as modrinth
import src.routers.v1.notes as notes
import src.routers.v1.pterodactyl as pterodactyl
import src.routers.v1.users as users

MODULES_V1 = [
    auth,
    jobs,
    knowledge,
    modpack,
    modrinth,
    notes,
    pterodactyl,
    users,
]

ROUTE_PREFIX = "/v1"
def include_routers_v1(app: FastAPI) -> None:
    app.include_router(auth.router, prefix=ROUTE_PREFIX, include_in_schema=False)
    app.include_router(jobs.router, prefix=ROUTE_PREFIX)
    app.include_router(knowledge.router, prefix=ROUTE_PREFIX)
    app.include_router(modpack.router, prefix=ROUTE_PREFIX)
    app.include_router(modrinth.router, prefix=ROUTE_PREFIX)
    app.include_router(notes.router, prefix=ROUTE_PREFIX)
    app.include_router(pterodactyl.router, prefix=ROUTE_PREFIX)
    app.include_router(users.router, prefix=ROUTE_PREFIX)

tags_metadata = [
    {
        "name": "modpack",
        "description": "Modpack endpoints. Query data and manage modpacks.",
    },
    {
        "name": "modrinth",
        "description": "Modrinth endpoints. Query data and manage Modrinth projects and versions.",
    },
    {
        "name": "pterodactyl",
        "description": "Pterodactyl endpoints. Query data and manage Pterodactyl servers.",
    },
    {
        "name": "jobs",
        "description": "Job endpoints. Follow the work the job worker runs.",
    },
    {
        "name": "users",
        "description": "User endpoints. Query user data.",
    }
]