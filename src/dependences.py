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
    AuditDatabase,
    DiagnosticsDatabase,
    HealthDatabase,
    JobDatabase,
    UsersDatabase,
    ModpackDatabase,
    ModrinthDatabase,
    NoteDatabase,
    PterodactylDatabase,
    UpdateCheckDatabase,
)
from src.services.audit import TargetNames
from src.services.jobs import WorkerBeat
from src.library.diagnostics import RECORDER
from src.services.notify import Notifier
from src.services.update import UpdateService
from src.services.modpack_import import ModpackImportService
from src.library.api.client.curseforge import CurseForgeAPI
from src.library.api.limiter import CURRENT_USER, RateLimiter
from src.services import (
    AuditService,
    DiagnosticsService,
    HealthService,
    JobService,
    UsersService,
    ModpackService,
    ModrinthService,
    NoteService,
    PterodactylService,
    UpdateCheckService,
)
from src.model.users import (
    Actor,
    UsersInternal
)
from src.library.auth.roles import actor_from_session
from src.services.users import OIDC_OPERATOR_GROUPS
from src.library.utils import getenv, getenv_opt
from src.exceptions import *

class Dependences(containers.DeclarativeContainer):
    authlib_oauth: providers.Singleton[OAuth] = providers.Singleton(OAuth)
    downloader = providers.Singleton(Downloader)
    # E4: 240 requests a minute for the whole app (Modrinth cuts at 300 per IP) and 60 per user.
    modrinthLimiter = providers.Singleton(RateLimiter, per_minute=240, per_user=60)
    modrinthAPI = providers.Singleton(ModrinthAPI, limiter=modrinthLimiter)
    # Optional (C1): the owner's own key; without it CurseForge is off and nothing calls it.
    curseforgeAPI = providers.Singleton(CurseForgeAPI, api_key=getenv_opt("CURSEFORGE_API_KEY"))
    pterodactylAPI = providers.Singleton(PterodactylAPI)
    loaderAPI = providers.Singleton(LoaderMetaAPI)

    healthDatabase = providers.Singleton(HealthDatabase)
    jobDatabase = providers.Singleton(JobDatabase)
    auditDatabase = providers.Singleton(AuditDatabase)
    diagnosticsDatabase = providers.Singleton(DiagnosticsDatabase)
    notifier = providers.Singleton(Notifier, jobDatabase)
    usersDatabase = providers.Singleton(UsersDatabase)
    modpackDatabase = providers.Singleton(ModpackDatabase)
    modrinthDatabase = providers.Singleton(ModrinthDatabase)
    pterodactylDatabase = providers.Singleton(PterodactylDatabase)
    updateCheckDatabase = providers.Singleton(UpdateCheckDatabase)
    noteDatabase = providers.Singleton(NoteDatabase)

    workerBeat = providers.Singleton(WorkerBeat)
    healthService = providers.Singleton(HealthService, healthDatabase, workerBeat)
    usersService = providers.Singleton(UsersService, usersDatabase)
    modrinthService = providers.Singleton(ModrinthService, modrinthAPI, modrinthDatabase)
    targetNames = providers.Singleton(TargetNames, modpackDatabase, pterodactylDatabase, modrinthDatabase)
    auditService = providers.Singleton(AuditService, auditDatabase, targetNames)
    # O7: the file of usage and errors; the version is the one the image was built with.
    diagnosticsService = providers.Singleton(DiagnosticsService, diagnosticsDatabase, version=getenv("VERSION", "dev"), recorder=providers.Object(RECORDER))
    pterodactylService = providers.Singleton(PterodactylService, pterodactylAPI, pterodactylDatabase, modrinthAPI, modrinthDatabase, modpackDatabase, auditService, jobDB=jobDatabase)
    modpackService = providers.Singleton(ModpackService, modpackDatabase, modrinthDatabase, modrinthAPI, loaderAPI, auditService, notifier, pterodactylDatabase, jobDB=jobDatabase)
    jobService = providers.Singleton(JobService, jobDatabase, pterodactylService, pterodactylDatabase, auditService, notifier)
    updateService = providers.Singleton(UpdateService, pterodactylAPI, pterodactylService, pterodactylDatabase, modpackDatabase, downloader, jobDatabase, auditService, notifier)
    modpackImportService = providers.Singleton(ModpackImportService, modpackService, modpackDatabase, modrinthDatabase, modrinthAPI, downloader, auditService, jobDB=jobDatabase, curseforge=curseforgeAPI)
    updateCheckService = providers.Singleton(UpdateCheckService, modpackDatabase, modrinthAPI, modrinthDatabase, updateCheckDatabase, jobDatabase, auditService, notifier)
    # O8 tanda 2: notes on builds and mods' versions.
    noteService = providers.Singleton(NoteService, noteDatabase, auditService, pterodactylDatabase)

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
    # The requests to Modrinth this request makes count against this user's quota (E4); a session
    # without an identity (legacy) is left unnamed, as before.
    try:
        actor = actor_from_session(request.session, OIDC_OPERATOR_GROUPS)
        CURRENT_USER.set(f"{actor.iss}|{actor.sub}")
    except HTTPException:
        pass
    return UsersInternal.model_validate_json(user)

async def get_current_actor(request: Request) -> Actor:
    actor = actor_from_session(request.session, OIDC_OPERATOR_GROUPS)
    # The requests to Modrinth this request makes count against this user's quota (E4).
    CURRENT_USER.set(f"{actor.iss}|{actor.sub}")
    return actor
