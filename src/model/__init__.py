from beanie import init_beanie
from pymongo import AsyncMongoClient
from src.model.modpack import ModpackBundle, ModpackProject
from src.model.modrinth import ModrinthProject, ModrinthVersion
from src.model.pterodactyl import PterodactylInstance, PterodactylServer
from src.model.users import UsersInternal
from src.library.utils import getenv

MONGODB_URI = getenv("MONGODB_URI")

async def configure_beanie() -> None:
    client: AsyncMongoClient = AsyncMongoClient(host=MONGODB_URI)
    await init_beanie(database=client.modpack, document_models=[ModpackBundle, ModpackProject])
    await init_beanie(database=client.modrinth, document_models=[ModrinthProject, ModrinthVersion])
    await init_beanie(database=client.pterodactyl, document_models=[PterodactylInstance, PterodactylServer])
    await init_beanie(database=client.userdata, document_models=[UsersInternal])