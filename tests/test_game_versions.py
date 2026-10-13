"""F09 (d-7985ee-a44a91): the Minecraft versions offered when creating a pack are Modrinth's releases,
newest first, asked at most once a day; when Modrinth fails, None for five minutes, so the form keeps its
text field and the page does not wait on Modrinth each time."""
import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any, cast

import pytest

from src.model.modrinth import ModrinthGameVersion
from src.services import modrinth as modrinth_service
from src.services.modrinth import ModrinthService

T0 = datetime(2026, 10, 13, tzinfo=timezone.utc)


def tag(version: str, kind: str = "release") -> ModrinthGameVersion:
    return ModrinthGameVersion(version=version, version_type=kind, date=T0, major=False)


class FakeAPI:
    def __init__(self, delay: float = 0.0) -> None:
        self.calls = 0
        self.fail = False
        self.delay = delay

    async def game_versions(self) -> list[ModrinthGameVersion]:
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.fail:
            raise ConnectionError("down")
        return [tag("26.3"), tag("26.3-pre1", "snapshot"), tag("1.21.11"), tag("b1.7.3", "beta")]


def service(api: FakeAPI, now: list[datetime]) -> ModrinthService:
    return ModrinthService(modrinthAPI=cast(Any, api), modrinthDB=cast(Any, None), clock=lambda: now[0])


def test_only_releases_newest_first_asked_once_a_day() -> None:
    api, now = FakeAPI(), [T0]
    s = service(api, now)

    async def run() -> list[Any]:
        first = await s.game_versions_release()
        now[0] = T0 + timedelta(hours=23)
        second = await s.game_versions_release()
        now[0] = T0 + timedelta(hours=25)
        await s.game_versions_release()
        return [first, second, api.calls]

    first, second, calls = asyncio.run(run())
    assert first == second == ["26.3", "1.21.11"] and calls == 2


def test_a_failure_is_none_and_is_remembered_for_five_minutes() -> None:
    # Batch B sweep (2026-10-13): with Modrinth down, every load of a server's page asked again and
    # waited for it. A failure is remembered briefly, then asked again.
    api, now = FakeAPI(), [T0]
    s = service(api, now)
    api.fail = True

    async def run() -> list[Any]:
        failed = await s.game_versions_release()
        api.fail = False
        now[0] = T0 + timedelta(minutes=4)
        still = await s.game_versions_release()
        now[0] = T0 + timedelta(minutes=6)
        return [failed, still, await s.game_versions_release(), api.calls]

    failed, still, then, calls = asyncio.run(run())
    assert failed is None and still is None and then == ["26.3", "1.21.11"] and calls == 2


def test_a_slow_modrinth_is_given_up_quickly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Final review of batch B: the pages that show the form wait for this call; a Modrinth that
    accepts and hangs must not hold them for the client's whole timeout."""
    monkeypatch.setattr(modrinth_service, "GAME_VERSIONS_TIMEOUT", 0.05)
    s = service(FakeAPI(delay=2.0), [T0])
    started = time.monotonic()
    assert asyncio.run(s.game_versions_release()) is None and time.monotonic() - started < 1.0


def test_pages_loading_at_once_share_one_request() -> None:
    api = FakeAPI(delay=0.05)
    s = service(api, [T0])

    async def run() -> list[Any]:
        return list(await asyncio.gather(*(s.game_versions_release() for _ in range(5))))

    assert asyncio.run(run()) == [["26.3", "1.21.11"]] * 5 and api.calls == 1
