"""Ninguna ruta de /v1 atiende sin sesión, salvo las del login (i-7985ee-c64e06).

Se arma la app con los routers de /v1 y se manda a cada ruta un request sin cookie: la respuesta
tiene que ser 401 antes de tocar la base o una API externa. Un router nuevo, o uno que se
reescriba al moverlo a su servicio, queda cubierto sin escribir un caso por ruta.
"""
import re

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

PUBLIC = {"/v1/login", "/v1/login/callback", "/v1/logout"}


def app() -> FastAPI:
    import main  # cablea el contenedor de dependencias
    from src.routers.v1 import include_routers_v1

    application = FastAPI()
    application.add_middleware(SessionMiddleware, secret_key="test")
    include_routers_v1(application)
    return application


def test_every_v1_route_refuses_a_request_without_a_session() -> None:
    application = app()
    client = TestClient(application)
    # openapi() y no app.routes: desde FastAPI 0.142 los routers incluidos son perezosos. Las
    # rutas del login no están en el esquema (include_in_schema=False).
    routes = [(method.upper(), path) for path, item in application.openapi()["paths"].items()
              for method in item if path not in PUBLIC]
    assert len(routes) > 15
    answers = {(method, path): client.request(method, re.sub(r"\{[^}]+\}", "x", path)).status_code
               for method, path in routes}
    assert {k: v for k, v in answers.items() if v != 401} == {}
