"""Solo lectura de verdad frente a Pterodactyl (tarjeta one-write-gate-makes-read-only-real).

Toda escritura del cliente de Pterodactyl pasa por `PterodactylAPI._write`, y un interruptor de
proceso (`PTERODACTYL_WRITES`, apagado por defecto) la rechaza antes de mandar nada, diga lo que
diga quien llama. El audit falla con una escritura fuera de la puerta.
"""
import asyncio
from typing import Any, Awaitable, Callable

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

import scripts.audit as audit
from src.exceptions import HTTP_403_FORBIDDEN
from src.library.api.client import pterodactyl as module
from src.library.api.client.pterodactyl import PterodactylAPI

pytestmark = pytest.mark.network

WRITES: dict[str, Callable[[PterodactylAPI], Awaitable[Any]]] = {
    "power": lambda api: api.server_power("abc", "stop"),
    "command": lambda api: api.server_command("abc", "stop"),
    "delete": lambda api: api.server_files_delete("abc", "/mods", ["a.jar"]),
    "upload": lambda api: api.server_files_upload("abc", "/mods", "a.jar", b"jar"),
}


def attempt(write: Callable[[PterodactylAPI], Awaitable[Any]]) -> tuple[Any, int]:
    """Lo que devuelve (o lanza) la escritura, y cuántos requests llegaron al servidor."""
    seen: list[str] = []

    async def handler(request: web.Request) -> web.StreamResponse:
        seen.append(f"{request.method} {request.path}")
        if request.path.endswith("/files/upload"):
            url = f"{request.url.origin()}/upload/file?token=jwt"
            return web.json_response({"object": "signed_url", "attributes": {"url": url}})
        return web.Response(status=204 if request.path.startswith("/api/") else 200)

    async def main() -> Any:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", handler)
        async with TestServer(app) as server:
            api = PterodactylAPI()
            api.base_url = str(server.make_url("/api/"))
            try:
                return await write(api)
            except HTTP_403_FORBIDDEN as refused:
                return refused

    return asyncio.run(main()), len([s for s in seen if not s.startswith("GET")])


@pytest.mark.parametrize("name", sorted(WRITES))
def test_with_writes_off_nothing_is_sent(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "PTERODACTYL_WRITES", False)
    result, writes_sent = attempt(WRITES[name])
    assert isinstance(result, HTTP_403_FORBIDDEN)
    assert writes_sent == 0


@pytest.mark.parametrize("name", sorted(WRITES))
def test_with_writes_on_the_write_is_sent(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "PTERODACTYL_WRITES", True)
    result, writes_sent = attempt(WRITES[name])
    assert result is True and writes_sent == 1


def test_writes_are_off_unless_configured() -> None:
    assert module.writes_enabled(None) is False
    assert module.writes_enabled("") is False
    assert module.writes_enabled("enabled") is True


def test_the_audit_fails_on_a_write_outside_the_gate() -> None:
    inside = ("async def _write(self):\n    await self._request(method=METHOD.POST, path='p')\n"
              "async def power(self):\n    await self._write(path='p')\n"
              "async def read(self):\n    await self._request(method=METHOD.GET, path='p')\n")
    assert audit.ungated_writes(inside, "c.py") == []
    # Naming a method for the gate is not a write outside it.
    renames = inside + "async def rename(self):\n    await self._write(path='p', method=METHOD.PUT)\n"
    assert audit.ungated_writes(renames, "c.py") == []
    outside = inside + "async def sneaky(self):\n    await self._request(method=METHOD.DELETE, path='p')\n"
    assert audit.ungated_writes(outside, "c.py") == ["c.py:7 sneaky: escribe en Pterodactyl sin pasar por _write"]
