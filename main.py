from src.library.utils import load_env
load_env(".env.yaml")

from src.routers.v1 import MODULES_V1, include_routers_v1, tags_metadata

import src.dependences as dependences
container = dependences.Dependences()
container.wire(
    modules=[
        *MODULES_V1,
        dependences
    ])

import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Modrinth Updater",
              version=os.getenv("VERSION", "1.0.0"),
              docs_url= os.getenv("DOCS_PATH", "/docs"),
              redoc_url= os.getenv("REDOCS_PATH"),
              openapi_tags=tags_metadata)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

include_routers_v1(app)