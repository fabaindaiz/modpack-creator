"""A build by /v1 after it is made (O8 B2, B3, D13 of d-7985ee-a99db7): what PATCH accepts."""
import asyncio
from typing import Any

from dependency_injector import providers
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.dependences import get_current_actor
from src.routers.v1 import include_routers_v1
from tests._actors import EDITOR


class FakeModpack:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    async def build_get(self, bundle_id: str, number: int) -> Any:
        return None

    async def build_star(self, bundle_id: str, number: int, starred: bool, actor: Any) -> Any:
        self.calls.append(("build_star", starred))

    async def build_stable(self, bundle_id: str, number: int, stable: bool, actor: Any) -> Any:
        self.calls.append(("build_stable", stable))


def patch(body: dict[str, Any]) -> tuple[int, list[tuple[Any, ...]]]:
    import main
    modpack = FakeModpack()
    main.container.modpackService.override(providers.Object(modpack))
    app = FastAPI()
    include_routers_v1(app)
    app.dependency_overrides[get_current_actor] = lambda: EDITOR

    async def send() -> int:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver") as client:
            return (await client.patch("/v1/modpack/b1/build/2", json=body)).status_code
    try:
        return asyncio.run(send()), modpack.calls
    finally:
        main.container.modpackService.reset_override()


def test_a_null_star_or_stable_mark_is_refused_not_a_silent_200() -> None:
    """`starred: null` answered 200 doing nothing (review of tanda 1): a mark is true or false; only the
    name takes null, to take it away."""
    assert patch({"starred": None}) == (422, [])
    assert patch({"stable": None}) == (422, [])
    assert patch({"starred": None, "stable": True})[0] == 422
