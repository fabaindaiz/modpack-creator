from src.library.utils import load_env, getenv, getenv_opt
load_env(".env.yaml")

from src.routers.v1 import MODULES_V1, include_routers_v1, tags_metadata
import src.dependences as dependences
container = dependences.Dependences()
container.wire(
    modules=[
        *MODULES_V1,
        dependences
    ])

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

import src.model as model
@asynccontextmanager
async def lifespan(app: FastAPI):
    dependences.configure_oauth2()
    await model.configure_beanie()
    include_routers_v1(app)
    yield

app = FastAPI(title="Modpack Creator API",
              version=getenv("VERSION", "1.0.0"),
              docs_url=getenv("DOCS_PATH", "/docs"),
              redoc_url=getenv_opt("REDOCS_PATH"),
              openapi_tags=tags_metadata,
              lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True)

app.add_middleware(
    SessionMiddleware,
    secret_key=getenv("SESSION_SECRET"))