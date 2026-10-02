"""Variables de entorno falsas para importar la app sin red.

`src/` lee su configuración al importarse (`getenv` lanza si falta una variable sin default),
así que esto tiene que correr antes de cualquier `import src...` o `import main`. Ningún test
abre la conexión a MongoDB ni al proveedor OIDC: eso ocurre en el `lifespan` de `main.py`,
que los tests no ejecutan.
"""
import os

FAKE_ENV = {
    "SESSION_SECRET": "test-session-secret",
    "MONGODB_URI": "mongodb://127.0.0.1:1",
    "PTERODACTYL_API_URL": "http://127.0.0.1:1/api/",
    "OIDC_CLIENT_ID": "test",
    "OIDC_CLIENT_SECRET": "test",
    "OIDC_METADATA_URL": "http://127.0.0.1:1/.well-known/openid-configuration",
    "OIDC_ALLOWED_GROUPS": "modpack-admins",
    "OIDC_OPERATOR_GROUPS": "modpack-operators",
}

for key, value in FAKE_ENV.items():
    os.environ[key] = value


import asyncio
import socket
from typing import Any, Awaitable, Callable, TypeVar
from uuid import uuid4

import pytest

LOCAL_HOSTS = (None, "localhost", "127.0.0.1", "::1")


@pytest.fixture(autouse=True)
def no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Un test que abre una conexión falla, en vez de pasar contra lo que haya del otro lado.

    La excepción se declara: `@pytest.mark.network`, para un test que usa a propósito un
    servidor local (MongoDB, un servidor aiohttp). Ni así se resuelve un nombre fuera de
    localhost: la resolución ocurre en C, debajo de `connect`, y saldría de la máquina.
    """
    real_getaddrinfo = socket.getaddrinfo

    def resolve_only_local(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host not in LOCAL_HOSTS:
            raise RuntimeError(f"un test intentó resolver {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve_only_local)
    if request.node.get_closest_marker("network") is not None:
        return

    def refuse(self: socket.socket, address: Any) -> None:
        raise RuntimeError(f"un test intentó abrir una conexión a {address!r}: marcarlo con @pytest.mark.network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)


# MongoDB real (i-7985ee-ade111): mongomock no simula el cliente async de PyMongo que usa
# Beanie 2 (docs/research/2026-10-arquitectura.md §2, tarjeta test-double-fidelity).

T = TypeVar("T")
MongoRunner = Callable[[Callable[[], Awaitable[T]]], T]


@pytest.fixture
def mongo(request: pytest.FixtureRequest) -> MongoRunner:
    """Corre un escenario async con Beanie inicializado sobre bases descartables.

    Cada llamada usa bases con un prefijo propio y las borra al terminar. Sin
    `TEST_MONGODB_URI` el test se salta en local y falla en CI: un gate que se salta
    sus tests de base en silencio está en verde y ciego.

    Dos cosas que este fixture hace a propósito, y que no hay que "optimizar":
    - `init_beanie` en cada llamada, no una vez por sesión. Beanie guarda estado en la clase
      del modelo, incluida la caché de `use_cache` (una por clase, con la consulta como clave y
      sin la base): reiniciarlo es lo que impide que un test lea lo que dejó otro.
    - Con los índices reales, sin `skip_indexes`: los `unique` del modelo son parte de lo que
      se prueba (i-7985ee-be55dd).
    """
    if request.node.get_closest_marker("network") is None:
        pytest.fail("un test que usa `mongo` se marca con @pytest.mark.network")
    uri = os.environ.get("TEST_MONGODB_URI")
    if not uri:
        message = "TEST_MONGODB_URI sin definir: ver scripts/test-mongo.sh"
        if os.environ.get("CI"):
            pytest.fail(message)
        pytest.skip(message)

    from pymongo import AsyncMongoClient
    from src.model import init_databases

    def run(scenario: Callable[[], Awaitable[T]]) -> T:
        async def main() -> T:
            client: AsyncMongoClient = AsyncMongoClient(uri, serverSelectionTimeoutMS=5000)
            prefix = f"test_{uuid4().hex[:12]}_"
            try:
                await init_databases(client, prefix)
                return await scenario()
            finally:
                for name in await client.list_database_names():
                    if name.startswith(prefix):
                        await client.drop_database(name)
                await client.close()
        return asyncio.run(main())

    return run
