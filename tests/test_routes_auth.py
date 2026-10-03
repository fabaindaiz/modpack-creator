"""Ninguna ruta de /v1 ni del panel atiende sin sesión, salvo las del login (i-7985ee-c64e06).

Se arma la app con los routers de /v1 y del panel, y se manda a cada ruta un request sin cookie:
en /v1 la respuesta tiene que ser 401; en el panel, 303 al login (o 401 con `HX-Redirect` si viene
de htmx). Todo antes de tocar la base o una API externa. Un router nuevo, o uno que se reescriba
al moverlo a su servicio, queda cubierto sin escribir un caso por ruta.
"""
import re
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
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


def web_app() -> FastAPI:
    import main  # cablea el contenedor de dependencias
    from src.routers.web import include_routers_web

    application = FastAPI()
    application.add_middleware(SessionMiddleware, secret_key="test")
    include_routers_web(application)
    return application


def test_every_web_route_sends_a_request_without_a_session_to_the_login() -> None:
    application = web_app()
    client = TestClient(application, follow_redirects=False)
    # app.routes y no openapi(): las rutas del panel no están en el esquema.
    routes = [(method, str(context.path)) for context in iter_route_contexts(application.routes)
              if isinstance(context.original_route, APIRoute) for method in sorted(context.methods or ())]
    assert ("GET", "/") in routes
    plain: dict[tuple[str, str], Any] = {}
    htmx: dict[tuple[str, str], Any] = {}
    for method, path in routes:
        url = re.sub(r"\{[^}]+\}", "x", path)
        plain[(method, path)] = client.request(method, url)
        htmx[(method, path)] = client.request(method, url, headers={"HX-Request": "true"})
    assert {k: r.status_code for k, r in plain.items()
            if r.status_code != 303 or not r.headers["location"].startswith("/v1/login?redirect=")} == {}
    assert {k: r.status_code for k, r in htmx.items()
            if r.status_code != 401 or not r.headers.get("hx-redirect", "").startswith("/v1/login?redirect=")} == {}


def test_login_and_logout_refuse_a_redirect_to_another_host() -> None:
    """`//evil.example` es una URL relativa al protocolo: el navegador la sigue a otro host."""
    client = TestClient(app(), follow_redirects=False)
    for path in ("/v1/login", "/v1/login/callback", "/v1/logout"):
        for bad in ("//evil.example", "/\\evil.example", "//"):
            assert client.get(path, params={"redirect": bad}).status_code == 422, (path, bad)


def test_logout_keeps_a_local_redirect() -> None:
    client = TestClient(app(), follow_redirects=False)
    for good in ("/", "/modpacks?x=1"):
        answer = client.get("/v1/logout", params={"redirect": good})
        assert answer.status_code == 307 and answer.headers["location"] == good


CASES = ["/", "/modpacks?x=1", "//h", "/\\h", "/%2F/h", "/x\n"]


def test_login_url_and_v1_share_one_rule() -> None:
    """El panel y /v1 deciden igual qué `redirect` es local (R11)."""
    from urllib.parse import parse_qs, urlsplit

    from src.library.auth.redirect import is_local_path
    from src.web import login_url

    client = TestClient(app(), follow_redirects=False)
    for value in CASES:
        accepted = client.get("/v1/logout", params={"redirect": value}).status_code != 422
        sent = parse_qs(urlsplit(login_url(value)).query)["redirect"][0]
        assert accepted == is_local_path(value), value
        assert (sent == value) == accepted or (not accepted and sent == "/"), value


def test_good_redirects_are_not_refused_by_v1_login() -> None:
    client = TestClient(app(), follow_redirects=False, raise_server_exceptions=False)
    for good in ("/", "/modpacks?x=1"):
        assert client.get("/v1/login", params={"redirect": good}).status_code != 422, good
