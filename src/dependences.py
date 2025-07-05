from dependency_injector import containers, providers
from fastapi.security import OAuth2PasswordBearer
from src.library.api.client.download import Downloader
from src.library.api.client.modrinth import ModrinthAPI
from src.library.api.client.pterodactyl import PterodactylAPI
from src.database.modrinth.project import ModrinthProjectDatabase
from src.database.modrinth.version import ModrinthVersionDatabase
from src.services.modrinth.project import ModrinthProjectService
from src.services.pterodactyl.server import PterodactylServerService

class Dependences(containers.DeclarativeContainer):
    downloader = providers.Singleton(Downloader)
    modrinthAPI = providers.Singleton(ModrinthAPI)
    pterodactylAPI = providers.Singleton(PterodactylAPI)

    modrinthProjectDatabase = providers.Singleton(ModrinthProjectDatabase)
    modrinthVersionDatabase = providers.Singleton(ModrinthVersionDatabase)

    modrinthProjectService = providers.Singleton(ModrinthProjectService, modrinthAPI, modrinthProjectDatabase)
    pterodactylServerService = providers.Singleton(PterodactylServerService, pterodactylAPI)

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="token",
    scopes={"me": "Read information about the current user.", "items": "Read items."},
)