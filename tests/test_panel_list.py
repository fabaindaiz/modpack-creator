"""La lista de servidores del panel, guardada (U6, dueño 2026-10-09): se pide como mucho cada 5 min,
al cargar si está vieja; «Actualizar lista» una vez por minuto; si el panel no responde, la guardada
con su antigüedad. Contra MongoDB real; el panel es un doble."""
from datetime import datetime, timedelta, timezone
from typing import Any, cast

import pytest

from src.database.pterodactyl import PterodactylDatabase
from src.exceptions import QuotaExceeded
from src.model.pterodactyl import PterodactylServer
from src.services.pterodactyl import PANEL_LIST_REFRESH_EVERY, PANEL_LIST_TTL, PterodactylService
from tests._actors import EDITOR, RecordingAudit
from tests.conftest import MongoRunner

pytestmark = pytest.mark.network


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now


class Panel:
    def __init__(self) -> None:
        self.asked = 0
        self.down = False

    async def server_get_many(self) -> list[PterodactylServer]:
        self.asked += 1
        if self.down:
            raise RuntimeError("panel down")
        return [PterodactylServer(identifier="id1", internal_id=1, uuid="uuid-1", name="Survival", node="n1",
                                  description="", status=None, is_suspended=False, is_installing=False,
                                  is_transferring=False)]


def service(panel: Panel, clock: Clock) -> PterodactylService:
    nothing = cast(Any, None)
    return PterodactylService(pterodactylAPI=cast(Any, panel), PterodactylDB=PterodactylDatabase(), modrinthAPI=nothing,
                              modrinthDB=nothing, modpackDB=nothing, audit=RecordingAudit(), clock=clock)


def test_the_list_is_asked_at_most_every_five_minutes_and_kept(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        panel, clock = Panel(), Clock()
        svc = service(panel, clock)
        first = await svc.panel_servers()
        clock.now += PANEL_LIST_TTL - timedelta(seconds=1)
        second = await svc.panel_servers()
        asked_inside = panel.asked
        clock.now += timedelta(seconds=2)
        await svc.panel_servers()
        return [s.uuid for s in first.servers], [s.uuid for s in second.servers], asked_inside, panel.asked

    first, second, inside, after = mongo(scenario)
    assert first == second == ["uuid-1"] and inside == 1 and after == 2


def test_a_panel_that_does_not_answer_shows_the_kept_list_with_its_age(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        panel, clock = Panel(), Clock()
        svc = service(panel, clock)
        panel.down = True
        nothing_kept = await svc.panel_servers()
        panel.down = False
        await svc.panel_servers()
        fetched = clock.now
        clock.now += timedelta(hours=2)
        panel.down = True
        kept = await svc.panel_servers()
        return nothing_kept, kept, fetched

    nothing_kept, kept, fetched = mongo(scenario)
    assert nothing_kept.failed and nothing_kept.servers == [] and nothing_kept.fetched_at is None
    assert kept.failed and [s.uuid for s in kept.servers] == ["uuid-1"] and kept.fetched_at == fetched


def test_the_refresh_asks_now_once_a_minute(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        panel, clock = Panel(), Clock()
        svc = service(panel, clock)
        await svc.panel_servers()
        clock.now += timedelta(seconds=20)
        with pytest.raises(QuotaExceeded) as early:
            await svc.panel_refresh(EDITOR)
        clock.now += PANEL_LIST_REFRESH_EVERY
        await svc.panel_refresh(EDITOR)
        return early.value.detail, panel.asked

    detail, asked = mongo(scenario)
    assert "Se puede volver a actualizar en 40 s" in str(detail) and asked == 2
