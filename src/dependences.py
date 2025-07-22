from authlib.integrations.starlette_client import OAuth, OAuthError
from dependency_injector import containers, providers
from dependency_injector.wiring import Provide, inject
from fastapi import HTTPException, Request, Depends
from src.library.api.client import (
    Downloader,
    ModrinthAPI,
    PterodactylAPI,
)
from src.database import (
    UsersDatabase,
    ModpackDatabase,
    ModrinthDatabase,
    PterodactylDatabase,
)
from src.services import (
    UsersService,
    ModpackService,
    ModrinthService,
    PterodactylService,
)
from src.model.users import (
    UsersInternal
)
from src.library.utils import getenv
from src.exceptions import *

class Dependences(containers.DeclarativeContainer):
    authlib_oauth: providers.Singleton[OAuth] = providers.Singleton(OAuth)
    downloader = providers.Singleton(Downloader)
    modrinthAPI = providers.Singleton(ModrinthAPI)
    pterodactylAPI = providers.Singleton(PterodactylAPI)

    usersDatabase = providers.Singleton(UsersDatabase)
    modpackDatabase = providers.Singleton(ModpackDatabase)
    modrinthDatabase = providers.Singleton(ModrinthDatabase)
    pterodactylDatabase = providers.Singleton(PterodactylDatabase)

    usersService = providers.Singleton(UsersService, usersDatabase)
    modrinthService = providers.Singleton(ModrinthService, modrinthAPI, modpackDatabase)
    pterodactylService = providers.Singleton(PterodactylService, pterodactylAPI, pterodactylDatabase)
    modpackService = providers.Singleton(ModpackService, modpackDatabase, modrinthDatabase)

@inject
def configure_oauth2(
        authlib_oauth: OAuth = Depends(Provide[Dependences.authlib_oauth])
        ) -> None:
    authlib_oauth.register(
        name='oauth2',
        client_id=getenv("OIDC_CLIENT_ID"),
        client_secret=getenv("OIDC_CLIENT_SECRET"),
        server_metadata_url=getenv("OIDC_METADATA_URL"),
        client_kwargs={'scope': 'openid email profile'},
    ) # type: ignore

@inject
async def get_current_user(
        request: Request,
        users: UsersService = Depends(Provide[Dependences.usersService])
        ) -> UsersInternal:
    session = request.session
    if not session:
        raise HTTP_401_UNAUTHORIZED("Not authenticated")
    return UsersInternal.model_validate_json(session.get('user', {}))