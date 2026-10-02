from authlib.integrations.starlette_client import OAuth, OAuthError
from dependency_injector import containers, providers
from dependency_injector.wiring import Provide, inject
from fastapi import HTTPException, Request, Depends
from src.library.api.client import (
    Downloader,
    LoaderMetaAPI,
    ModrinthAPI,
    PterodactylAPI,
)
from src.database import (
    HealthDatabase,
    JobDatabase,
    UsersDatabase,
    ModpackDatabase,
    ModrinthDatabase,
    PterodactylDatabase,
)
from src.services.notify import Notifier
from src.services.update import UpdateService
from src.services import (
    HealthService,
    JobService,
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
    loaderAPI = providers.Singleton(LoaderMetaAPI)

    healthDatabase = providers.Singleton(HealthDatabase)
    jobDatabase = providers.Singleton(JobDatabase)
    notifier = providers.Singleton(Notifier, jobDatabase)
    usersDatabase = providers.Singleton(UsersDatabase)
    modpackDatabase = providers.Singleton(ModpackDatabase)
    modrinthDatabase = providers.Singleton(ModrinthDatabase)
    pterodactylDatabase = providers.Singleton(PterodactylDatabase)

    healthService = providers.Singleton(HealthService, healthDatabase)
    usersService = providers.Singleton(UsersService, usersDatabase)
    modrinthService = providers.Singleton(ModrinthService, modrinthAPI, modrinthDatabase)
    pterodactylService = providers.Singleton(PterodactylService, pterodactylAPI, pterodactylDatabase, modrinthAPI, modrinthDatabase, modpackDatabase)
    modpackService = providers.Singleton(ModpackService, modpackDatabase, modrinthDatabase, modrinthAPI, loaderAPI, notifier)
    jobService = providers.Singleton(JobService, jobDatabase, pterodactylService, pterodactylDatabase, notifier)
    updateService = providers.Singleton(UpdateService, pterodactylAPI, pterodactylService, pterodactylDatabase, modpackDatabase, downloader, jobDatabase, notifier)

async def close_clients(container: Dependences) -> None:
    """Close the HTTP session each client keeps (i-7985ee-57034c); the lifespan calls it on shutdown."""
    for client in (container.downloader(), container.modrinthAPI(), container.pterodactylAPI(), container.loaderAPI()):
        await client.close()
    await container.notifier().close()

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
    user = request.session.get('user')
    if not user:
        raise HTTP_401_UNAUTHORIZED("Not authenticated")
    return UsersInternal.model_validate_json(user)