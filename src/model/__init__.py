from beanie import init_beanie
from pymongo import AsyncMongoClient
from pymongo.asynchronous.collection import AsyncCollection
from src.model.audit import AuditEvent
from src.model.diagnostics import DiagnosticEvent
from src.model.jobs import Job
from src.model.modpack import LoaderVersion, ModpackBuild, ModpackBundle, ModpackReplacement, ModpackResolution, UpdateCheck
from src.model.modrinth import ModrinthGone, ModrinthProject, ModrinthSearch, ModrinthUnknown, ModrinthVersion, ModrinthVersionList
from src.model.pterodactyl import PanelServerList, PterodactylInstance, PterodactylServer, ServerScan
from src.model.users import UsersInternal
from src.library.utils import getenv

MONGODB_URI = getenv("MONGODB_URI")

async def drop_unique_index(collection: AsyncCollection, name: str) -> None:
    """Drop an index that is no longer unique in the model, so init_beanie can recreate it.

    init_beanie refuses an index whose name exists with other options, and never drops it.
    Dropping an index loses no data, and this is a no-op once done.
    """
    index = (await collection.index_information()).get(name)
    if index and index.get("unique"):
        await collection.drop_index(name)

async def init_databases(client: AsyncMongoClient, prefix: str = "") -> None:
    """Register every Document on its database; tests pass a prefix for throwaway databases."""
    # i-7985ee-be55dd: a project has many versions. Beanie names collections after the class
    # (Settings.collection is not a Beanie 2 setting), so the collection is ModrinthVersion.
    await drop_unique_index(client[f"{prefix}modrinth"]["ModrinthVersion"], "project_id_1")
    await init_beanie(database=client[f"{prefix}modpack"], document_models=[ModpackBundle, ModpackBuild, ModpackReplacement, ModpackResolution, UpdateCheck, LoaderVersion])
    await init_beanie(database=client[f"{prefix}modrinth"], document_models=[ModrinthProject, ModrinthVersion, ModrinthVersionList, ModrinthUnknown, ModrinthSearch, ModrinthGone])
    await init_beanie(database=client[f"{prefix}pterodactyl"], document_models=[PterodactylInstance, PterodactylServer, ServerScan, PanelServerList])
    await init_beanie(database=client[f"{prefix}userdata"], document_models=[UsersInternal])
    await init_beanie(database=client[f"{prefix}jobs"], document_models=[Job])
    await init_beanie(database=client[f"{prefix}audit"], document_models=[AuditEvent])
    await init_beanie(database=client[f"{prefix}diagnostics"], document_models=[DiagnosticEvent])

async def configure_beanie() -> None:
    client: AsyncMongoClient = AsyncMongoClient(host=MONGODB_URI)
    await init_databases(client)
