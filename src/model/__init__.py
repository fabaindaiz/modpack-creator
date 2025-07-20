from beanie import init_beanie
from pymongo import AsyncMongoClient
from src.model.local.project import LocalProject
from src.model.modrinth.project import ModrinthProject
from src.model.modrinth.version import ModrinthVersion
from src.model.pterodactyl.instance import PterodactylInstance
from src.model.pterodactyl.server import PterodactylServer
from src.model.userdata.users import UsersInternal
from src.library.utils import getenv

MONGODB_URI = getenv("MONGODB_URI")

async def configure_beanie() -> None:
    client: AsyncMongoClient = AsyncMongoClient(host=MONGODB_URI)
    #await init_beanie(database=client.modpack, document_models=[LocalProject])
    await init_beanie(database=client.project, document_models=[ModrinthProject, ModrinthVersion])
    await init_beanie(database=client.userdata, document_models=[UsersInternal])
    #await init_beanie(database=client.pterodactyl, document_models=[PterodactylInstance, PterodactylServer])

__all__ = [
    "LocalProject",
    "ModrinthProject",
    "ModrinthVersion",
    "PterodactylInstance",
    "PterodactylServer",
    "UsersInternal",
    "configure_beanie",
]