from src.library.utils import load_env, getenv, getenv_opt, strToBool
load_env(".env.yaml")

# JSON logs with the request id (i-7985ee-90f944), before anything logs.
from src.library.logging import RequestIdMiddleware, configure_logging
configure_logging(getenv("LOG_LEVEL", "INFO"))

import src.routers.health as health
from src.routers.v1 import MODULES_V1, include_routers_v1, tags_metadata
import src.dependences as dependences
container = dependences.Dependences()
container.wire(
    modules=[
        health,
        *MODULES_V1,
        dependences
    ])

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

import src.model as model
import asyncio
from src.services.jobs import JobWorker

@asynccontextmanager
async def lifespan(app: FastAPI):
    dependences.configure_oauth2()
    await model.configure_beanie()
    include_routers_v1(app)
    # The job worker (i-7985ee-0ec295), unless JOBS_WORKER is "off". Several processes may run
    # one each: claims are atomic and leases expire.
    stop = asyncio.Event()
    worker = None
    if getenv("JOBS_WORKER", "on").strip().lower() != "off":
        worker = asyncio.create_task(
            JobWorker(container.jobDatabase(), container.jobService().handlers()).run_forever(stop))
    yield
    stop.set()
    if worker is not None:
        await worker
    await dependences.close_clients(container)

app = FastAPI(title="Modpack Creator API",
              version=getenv("VERSION", "1.0.0"),
              docs_url=getenv("DOCS_PATH", "/docs"),
              redoc_url=getenv_opt("REDOCS_PATH"),
              openapi_tags=tags_metadata,
              lifespan=lifespan)
# Mounted at import: it needs nothing the lifespan sets up, and has no session (i-7985ee-90f944).
app.include_router(health.router)

# CORS only for origins named explicitly: with credentials, a wildcard makes Starlette echo any
# Origin back (d-7985ee-4e4aee). Without CORS_ALLOW_ORIGINS there is no cross-origin access.
from src.library.auth import parse_allowlist
CORS_ALLOW_ORIGINS = parse_allowlist(getenv_opt("CORS_ALLOW_ORIGINS"))
if CORS_ALLOW_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(CORS_ALLOW_ORIGINS),
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True)

# The session cookie is HTTPS-only unless SESSION_HTTPS_ONLY says otherwise, and lasts hours, not
# Starlette's default of 14 days. same_site stays "lax": "strict" breaks the OIDC callback.
app.add_middleware(
    SessionMiddleware,
    secret_key=getenv("SESSION_SECRET"),
    https_only=strToBool(getenv("SESSION_HTTPS_ONLY", "true")),
    max_age=int(getenv("SESSION_MAX_AGE", "43200")),
    same_site="lax")

# Added last, so it runs first: every request, including the session's 401s, gets an id and a line.
app.add_middleware(RequestIdMiddleware)
