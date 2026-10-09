"""La app se compone en Python 3.12: importa, y el contenedor de dependencias cablea cada
servicio con los tipos que su constructor declara.

Es la clase de error que nadie ve antes del despliegue: un import roto o una dependencia
cruzada solo fallan al arrancar el contenedor en el servidor.
"""
import inspect
import typing

from dependency_injector import providers


def test_app_imports() -> None:
    import main

    assert main.app.title == "Modpack Creator API"


def test_v1_routers_mount() -> None:
    # main.py los monta en el lifespan, que abre MongoDB; aquí se montan sobre una app vacía.
    from fastapi import FastAPI
    from src.routers.v1 import include_routers_v1

    app = FastAPI()
    include_routers_v1(app)
    # app.openapi() y no app.routes: desde FastAPI 0.142 los routers incluidos son perezosos.
    paths = set(app.openapi()["paths"])
    assert {"/v1/modpack", "/v1/modrinth/project", "/v1/server", "/v1/users/me"} <= paths


def test_di_wiring_matches_constructor_types() -> None:
    from src.dependences import Dependences

    checked = 0
    for name, provider in Dependences.providers.items():
        if not isinstance(provider, providers.Singleton) or not provider.args:
            continue
        cls = typing.cast(type, provider.provides)
        init = getattr(cls, "__init__")
        hints = typing.get_type_hints(init)
        params = [p for p in inspect.signature(init).parameters.values() if p.name != "self"]
        required = [p for p in params if p.default is inspect.Parameter.empty]
        # Every required parameter is injected; an optional one may be left to its default.
        assert len(required) <= len(provider.args) <= len(params), (
            f"{name}: {len(provider.args)} args for {len(required)} required of {len(params)} parameters")
        for param, arg in zip(params, provider.args):
            injected = arg.provides
            expected = typing.get_args(hints[param.name])[0] if typing.get_origin(hints[param.name]) in (typing.Union, __import__("types").UnionType) else hints[param.name]
            assert issubclass(injected, expected), (
                f"{name}: {param.name} expects {expected.__name__}, gets {injected.__name__}")
            checked += 1
    assert checked > 0


def test_shutdown_closes_the_session_of_every_http_client() -> None:
    import asyncio

    import main
    from src.dependences import close_clients

    async def scenario() -> list[bool]:
        clients = [main.container.downloader(), main.container.modrinthAPI(), main.container.pterodactylAPI()]
        for client in clients:
            client._client_session()
        await close_clients(main.container)
        return [client._session is None for client in clients]

    assert asyncio.run(scenario()) == [True, True, True]


def test_the_oidc_client_uses_httpx2_not_the_deprecated_httpx_fallback() -> None:
    """Authlib 1.8 deprecó su integración con httpx en favor de httpx2, y cae a httpx solo si
    httpx2 no está (authlib/integrations/httpx_client/_compat.py)."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-W", "error::DeprecationWarning", "-c",
         "import authlib.integrations.starlette_client, authlib.integrations.httpx_client._compat as c; "
         "print(c.httpx2.__name__)"],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-500:]
    assert result.stdout.strip() == "httpx2"


def test_the_modrinth_client_takes_its_turn_in_one_limiter_of_240_per_minute_and_60_per_user() -> None:
    import main
    from src.library.api.limiter import RateLimiter

    limiter = main.container.modrinthAPI().limiter
    assert isinstance(limiter, RateLimiter) and (limiter.per_minute, limiter.per_user) == (240, 60)
