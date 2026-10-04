"""La programación diaria de la revisión de actualizaciones (N6).

`due_today` es pura: se prueba con instantes reales alrededor de los cambios de horario de Chile
(`America/Santiago`), donde la fecha en UTC y la local difieren. `parse_check_at` es estricta y un
valor malo hace fallar el arranque, no el primer día que debía correr.
"""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from beanie import PydanticObjectId

from src.database.jobs import JobDatabase
from src.database.modpack import ModpackDatabase
from src.database.modrinth import ModrinthDatabase
from src.database.update_check import UpdateCheckDatabase
from src.model.modpack import ModpackBuild, ModpackBundle
from src.model.modpack.update_check import UpdateCheck
from src.model.utils import MinecraftLoaders
from src.services.update_check import UpdateCheckService, due_today, parse_check_at, schedule_from_env
from tests._actors import RecordingAudit
from tests.conftest import MongoRunner

SCL = ZoneInfo("America/Santiago")
AT = time(4, 0)


def local(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    """A wall-clock time in Santiago, as the UTC instant it is."""
    return datetime(year, month, day, hour, minute, tzinfo=SCL).astimezone(timezone.utc)


# parse_check_at.

@pytest.mark.parametrize("value, expected", [("04:00", time(4, 0)), ("00:00", time(0, 0)), ("23:59", time(23, 59)),
                                             (" 04:30 ", time(4, 30))])
def test_parse_check_at_accepts_hh_mm(value: str, expected: time) -> None:
    assert parse_check_at(value) == expected


@pytest.mark.parametrize("value", ["4:00", "24:00", "aa", "", "04:60", "04:0", "04:00:00", "0400", "4 am"])
def test_parse_check_at_refuses_anything_else_with_a_clear_message(value: str) -> None:
    with pytest.raises(ValueError, match="UPDATE_CHECK_AT.*HH:MM"):
        parse_check_at(value)


# due_today.

def test_before_the_hour_it_is_not_due_even_if_never_checked() -> None:
    assert not due_today(local(2026, 7, 10, 3, 59), AT, SCL, None)


def test_after_the_hour_a_modpack_never_checked_is_due() -> None:
    assert due_today(local(2026, 7, 10, 4, 0), AT, SCL, None)


def test_yesterdays_check_is_due_after_the_hour_and_todays_is_not() -> None:
    now = local(2026, 7, 10, 9, 0)
    assert due_today(now, AT, SCL, local(2026, 7, 9, 4, 5))
    assert not due_today(now, AT, SCL, local(2026, 7, 10, 4, 5))


def test_a_manual_check_after_midnight_counts_as_todays() -> None:
    assert not due_today(local(2026, 7, 10, 9, 0), AT, SCL, local(2026, 7, 10, 0, 30))


def test_a_naive_last_check_is_read_as_utc() -> None:
    now = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    assert due_today(now, AT, SCL, datetime(2026, 7, 9, 12, 0))
    assert not due_today(now, AT, SCL, datetime(2026, 7, 10, 9, 0))


def test_the_utc_date_is_never_the_local_one_in_the_evening() -> None:
    # 22:00 in Santiago in July is 02:00 UTC of the next day.
    now = local(2026, 7, 10, 22, 0)
    assert now.date() != now.astimezone(SCL).date()
    assert not due_today(now, AT, SCL, local(2026, 7, 10, 4, 10))
    assert due_today(now, AT, SCL, local(2026, 7, 9, 22, 0))


def test_the_offset_really_changes_in_april_and_september() -> None:
    assert local(2026, 4, 3, 12, 0).astimezone(SCL).utcoffset() == timedelta(hours=-3)
    assert local(2026, 4, 6, 12, 0).astimezone(SCL).utcoffset() == timedelta(hours=-4)
    assert local(2026, 9, 5, 12, 0).astimezone(SCL).utcoffset() == timedelta(hours=-4)
    assert local(2026, 9, 7, 12, 0).astimezone(SCL).utcoffset() == timedelta(hours=-3)


def test_september_change_a_check_late_on_the_eve_does_not_hide_the_next_day() -> None:
    # Clocks go forward the night of Sep 5-6. A check at 23:30 of the 5th is 03:30Z of the 6th.
    last = local(2026, 9, 5, 23, 30)
    assert last.date() == datetime(2026, 9, 6).date()  # UTC date of the next day
    assert due_today(local(2026, 9, 6, 4, 30), AT, SCL, last)
    assert not due_today(local(2026, 9, 6, 3, 59), AT, SCL, last)


def test_september_change_after_it_ran_today_it_does_not_run_again() -> None:
    last = local(2026, 9, 6, 4, 5)
    assert not due_today(local(2026, 9, 6, 23, 0), AT, SCL, last)
    assert due_today(local(2026, 9, 7, 4, 0), AT, SCL, last)


def test_april_change_a_check_before_it_and_the_day_after() -> None:
    # Clocks go back the night of Apr 4-5.
    assert due_today(local(2026, 4, 5, 4, 0), AT, SCL, local(2026, 4, 4, 4, 5))
    last = local(2026, 4, 5, 4, 10)
    assert not due_today(local(2026, 4, 5, 23, 50), AT, SCL, last)
    assert not due_today(local(2026, 4, 5, 23, 59), AT, SCL, last)
    assert due_today(local(2026, 4, 6, 4, 0), AT, SCL, last)


def test_april_change_the_repeated_hour_does_not_run_twice() -> None:
    # At 23:30 of Apr 4 (-3) a check ran; an hour later the wall clock reads 23:30 again (-4).
    last = datetime(2026, 4, 5, 2, 30, tzinfo=timezone.utc)
    again = datetime(2026, 4, 5, 3, 30, tzinfo=timezone.utc)
    assert last.astimezone(SCL).hour == again.astimezone(SCL).hour == 23
    assert not due_today(again, time(23, 0), SCL, last)


# La configuración del arranque.

def test_without_update_check_at_there_is_no_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UPDATE_CHECK_AT", raising=False)
    assert schedule_from_env() is None


def test_the_schedule_defaults_to_santiago(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UPDATE_CHECK_AT", "04:00")
    monkeypatch.delenv("UPDATE_CHECK_TZ", raising=False)
    assert schedule_from_env() == (time(4, 0), ZoneInfo("America/Santiago"))


def test_a_bad_time_or_an_unknown_zone_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UPDATE_CHECK_AT", "4am")
    with pytest.raises(ValueError, match="UPDATE_CHECK_AT"):
        schedule_from_env()
    monkeypatch.setenv("UPDATE_CHECK_AT", "04:00")
    monkeypatch.setenv("UPDATE_CHECK_TZ", "Mars/Olympus")
    with pytest.raises(ValueError, match="UPDATE_CHECK_TZ.*Mars/Olympus"):
        schedule_from_env()


def test_the_app_does_not_start_with_a_bad_update_check_at(monkeypatch: pytest.MonkeyPatch) -> None:
    import main
    from fastapi.testclient import TestClient

    monkeypatch.setenv("UPDATE_CHECK_AT", "25:00")
    with pytest.raises(ValueError, match="UPDATE_CHECK_AT"):
        with TestClient(main.app):
            pass


# El programador, contra MongoDB real.

def make() -> UpdateCheckService:
    return UpdateCheckService(ModpackDatabase(), None, ModrinthDatabase(), UpdateCheckDatabase(), JobDatabase(),  # type: ignore[arg-type]
                              RecordingAudit())


async def bundle(name: str, recommended: int | None = None, latest: int | None = None) -> str:
    created = await ModpackDatabase().bundle_insert(ModpackBundle(
        name=name, description="", version="26.3", loader=MinecraftLoaders.fabric,
        latest_build=latest, recommended_build=recommended))
    return str(created.id)


@pytest.mark.network
def test_the_scheduler_queues_a_check_per_due_modpack_with_no_actor_and_skips_the_rest(mongo: MongoRunner) -> None:
    now = local(2026, 7, 10, 9, 0)

    async def scenario() -> tuple[list[str], list[str | None], list[str]]:
        due = await bundle("Due", recommended=1, latest=1)
        checked_today = await bundle("Hoy", latest=2)
        yesterday = await bundle("Ayer", latest=1)
        await bundle("SinBuild")
        for bundle_id, when in ((checked_today, local(2026, 7, 10, 4, 5)), (yesterday, local(2026, 7, 9, 4, 5))):
            await UpdateCheck(bundle_id=PydanticObjectId(bundle_id), build_number=1, checked_at=when).insert()
        service = make()
        first = await service.schedule(now, AT, SCL)
        second = await service.schedule(now, AT, SCL)  # the jobs are still active: 409s, ignored
        return ([j.payload["bundle_id"] for j in first], [j.requested_by.sub if j.requested_by else None for j in first],
                [j.payload["bundle_id"] for j in second] + [due, yesterday])

    queued, who, rest = mongo(scenario)
    assert len(queued) == 2 and who == [None, None]
    assert rest[:-2] == [] and set(queued) == set(rest[-2:])


@pytest.mark.network
def test_the_scheduler_does_not_queue_before_the_hour(mongo: MongoRunner) -> None:
    async def scenario() -> int:
        await bundle("Due", recommended=1, latest=1)
        return len(await make().schedule(local(2026, 7, 10, 3, 0), AT, SCL))

    assert mongo(scenario) == 0
