"""F09 (d-7985ee-a44a91): the Minecraft versions offered when creating a pack are Modrinth's releases,
newest first, asked at most once a day; when Modrinth fails, None, so the form keeps its text field."""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from src.model.modrinth import ModrinthGameVersion
from src.services.modrinth import ModrinthService

T0 = datetime(2026, 10, 13, tzinfo=timezone.utc)


def tag(version: str, kind: str = "release") -> ModrinthGameVersion:
    return ModrinthGameVersion(version=version, version_type=kind, date=T0, major=False)


class FakeAPI:
    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    async def game_versions(self) -> list[ModrinthGameVersion]:
        self.calls += 1
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


def test_a_failure_is_none_and_is_not_kept() -> None:
    api, now = FakeAPI(), [T0]
    s = service(api, now)
    api.fail = True

    async def run() -> list[Any]:
        failed = await s.game_versions_release()
        api.fail = False
        return [failed, await s.game_versions_release()]

    failed, then = asyncio.run(run())
    assert failed is None and then == ["26.3", "1.21.11"]
