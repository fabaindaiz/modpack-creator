from src.library.utils import load_env, getenv
load_env(".env.yaml")

from src.routers.v1 import MODULES_V1, include_routers_v1, tags_metadata
import src.dependences as dependences
container = dependences.Dependences()
container.wire(
    modules=[
        *MODULES_V1,
        dependences
    ])
dependences.configure_oauth2()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

app = FastAPI(title="Modpack Creator API",
              version=getenv("VERSION", "1.0.0"),
              docs_url= getenv("DOCS_PATH", "/docs"),
              redoc_url= getenv("REDOCS_PATH"),
              openapi_tags=tags_metadata)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(
    SessionMiddleware,
    secret_key=getenv("SESSION_SECRET"),
)

include_routers_v1(app)