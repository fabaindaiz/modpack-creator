from authlib.integrations.starlette_client import OAuth, OAuthError
from dependency_injector import containers, providers
from dependency_injector.wiring import Provide, inject
from fastapi import HTTPException, Request, status, Depends
from src.library.api.client.download import Downloader
from src.library.api.client.modrinth import ModrinthAPI
from src.library.api.client.pterodactyl import PterodactylAPI
from src.database.userdata.users import UsersDatabase
from src.services.userdata.users import UsersService
from src.database.modrinth.project import ModrinthProjectDatabase
from src.database.modrinth.version import ModrinthVersionDatabase
from src.database.pterodactyl.server import PterodactylServerDatabase
from src.services.modrinth.project import ModrinthProjectService
from src.services.pterodactyl.server import PterodactylServerService
from src.model.userdata.users import UsersInternal
from src.library.utils import getenv

class Dependences(containers.DeclarativeContainer):
    authlib_oauth = providers.Singleton(OAuth)

    downloader = providers.Singleton(Downloader)
    modrinthAPI = providers.Singleton(ModrinthAPI)
    pterodactylAPI = providers.Singleton(PterodactylAPI)

    usersDatabase = providers.Singleton(UsersDatabase)
    usersService = providers.Singleton(UsersService, usersDatabase)

    modrinthProjectDatabase = providers.Singleton(ModrinthProjectDatabase)
    modrinthVersionDatabase = providers.Singleton(ModrinthVersionDatabase)
    pterodactylServerDatabase = providers.Singleton(PterodactylServerDatabase)

    modrinthProjectService = providers.Singleton(ModrinthProjectService, modrinthAPI, modrinthProjectDatabase)
    pterodactylServerService = providers.Singleton(PterodactylServerService, pterodactylAPI)

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
