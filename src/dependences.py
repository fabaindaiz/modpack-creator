from authlib.integrations.starlette_client import OAuth, OAuthError
from beanie import init_beanie
from dependency_injector import containers, providers
from dependency_injector.wiring import Provide, inject
from fastapi import HTTPException, Request, status, Depends
from motor.motor_asyncio import AsyncIOMotorClient
from src.library.api.client.download import Downloader
from src.library.api.client.modrinth import ModrinthAPI
from src.library.api.client.pterodactyl import PterodactylAPI
from src.services.modrinth.project import ModrinthProjectService
from src.services.pterodactyl.server import PterodactylServerService
from src.services.userdata.users import UsersService
from src.model.userdata.users import UsersInternal
from src.library.utils import getenv

MONGODB_URI = getenv("MONGODB_URI")

class Dependences(containers.DeclarativeContainer):
    authlib_oauth: providers.Singleton[OAuth] = providers.Singleton(OAuth)
    downloader = providers.Singleton(Downloader)
    modrinthAPI = providers.Singleton(ModrinthAPI)
    pterodactylAPI = providers.Singleton(PterodactylAPI)

    usersService = providers.Singleton(UsersService)
    modrinthProjectService = providers.Singleton(ModrinthProjectService, modrinthAPI)
    pterodactylServerService = providers.Singleton(PterodactylServerService, pterodactylAPI)

from src.model import (
    LocalProject,
    ModrinthProject,
    ModrinthVersion,
    PterodactylInstance,
    PterodactylServer,
    UsersInternal,
)

@inject
async def configure_beanie() -> None:
    client: AsyncIOMotorClient = AsyncIOMotorClient(host=MONGODB_URI)
    await init_beanie(database=client.local, document_models=[LocalProject])
    await init_beanie(database=client.modrinth, document_models=[ModrinthProject])
    await init_beanie(database=client.modrinth, document_models=[ModrinthVersion])
    await init_beanie(database=client.pterodactyl, document_models=[PterodactylInstance])
    await init_beanie(database=client.pterodactyl, document_models=[PterodactylServer])
    await init_beanie(database=client.users, document_models=[UsersInternal])

@inject
def configure_oauth2(
        authlib_oauth: OAuth = Depends(Provide[Dependences.authlib_oauth])
        ) -> None:
    authlib_oauth.register(
        name='oauth2',
        client_id=getenv("OIDC_CLIENT_ID"),
        client_secret=getenv("OIDC_CLIENT_SECRET"),
        server_metadata_url=getenv("OIDC_METADATA_URL"),
        client_kwargs={
            'scope': 'openid email profile'
        },
    )

async def get_current_user(
        request: Request,
        ) -> UsersInternal:
    session = request.session
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return UsersInternal(**session["user"])