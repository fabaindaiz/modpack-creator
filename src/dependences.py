from dependency_injector import containers, providers
from fastapi.security import OAuth2PasswordBearer
from src.library.api.client.pterodactyl import PterodactylAPI
from src.library.api.client.modrinth import ModrinthAPI, ModrinthCDN
from src.services.server import ServerService

class Dependences(containers.DeclarativeContainer):
    pterodactylAPI = providers.Singleton(PterodactylAPI)
    modrinthAPI = providers.Singleton(ModrinthAPI)

    serverService = providers.Singleton(ServerService, pterodactylAPI)

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="token",
    scopes={"me": "Read information about the current user.", "items": "Read items."},
)