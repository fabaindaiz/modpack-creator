"""A mod's history in a pack (O8 tanda 2: D2 of d-7985ee-a99db7, T2 and T3 of d-7985ee-16be41; user-walk
N5, N6, N17): only what changed, runs of builds with the same version grouped, newest first."""
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from src.library.history import BuildPoint, PinEvent, PresenceEvent, ScanPoint, mod_history
from src.model.modpack import PastOrigin, PinOrigin, PinSource

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def day(n: int) -> datetime:
    return T0 + timedelta(days=n)


def texts(entries: list[Any]) -> list[str]:
    return [e.text for e in entries]


def test_history_keeps_only_changes_and_groups_runs() -> None:
    builds = [BuildPoint(number=n, at=day(n), version=v, version_id="id-" + v if v else None, test=n == 5, stable=n == 2)
              for n, v in ((1, "1.6.2"), (2, "1.6.4"), (3, "1.6.4"), (4, "1.6.4"), (5, "1.7.2"))]
    scans = [ScanPoint(server="Survival", at=day(n), version=v) for n, v in ((2, "1.6.4"), (3, "1.6.4"), (6, "1.7.2"))]
    assert texts(mod_history(builds, scans, [], [], [], [])) == [
        "Servidor Survival · 1.6.4 → 1.7.2",
        "Build 5 · 1.7.2 · prueba",
        "Builds 2–4 · 1.6.4 · estable",
        "Servidor Survival · 1.6.4",
        "Build 1 · 1.6.2",
    ]


def test_history_says_pins_past_origins_and_removal() -> None:
    pins = [PinEvent(at=day(3), author="Eddie", from_version="1.6.2", to_version="1.6.4", known=True),
            PinEvent(at=day(5), author="Eddie", from_version=None, to_version=None, known=False)]
    past = [PastOrigin(origin=PinOrigin(source=PinSource.pack, version_id="p", version_number="1.6.0", label="Pack.mrpack"),
                       until=day(2))]
    presence = [PresenceEvent(at=day(6), added=False), PresenceEvent(at=day(7), added=True)]
    assert texts(mod_history([], [], pins, past, [], presence)) == [
        "Vuelto a agregar al pack",
        "Quitado del pack",
        "Sin fijar · Eddie (antes: desconocida)",
        "Fijado 1.6.4 · Eddie (antes: 1.6.2)",
        "Origen pasado: Pack.mrpack 1.6.0",
    ]


def test_a_note_is_an_entry_with_its_note() -> None:
    from tests.test_web_modpacks import note
    found = note("fails", "pantalla negra", project_id="P", version_id="id-1.7.2", version_number="1.7.2")
    [entry] = mod_history([], [], [], [], [found], [])
    assert entry.kind == "note" and entry.note is found and entry.version == "1.7.2"


@pytest.mark.network
def test_mod_history_leaves_out_another_packs_scans_and_never_calls_modrinth(mongo: Any) -> None:
    from src.database.pterodactyl import PterodactylDatabase
    from src.library.scan import ScanFile
    from src.model.pterodactyl import PterodactylServer, ServerScan
    from src.model.modpack import ProjectOrigin
    from tests._actors import EDITOR
    from tests._modrinth import IRIS
    from tests.test_builds import empty_bundle

    def jar(version: str) -> ScanFile:
        return ScanFile(path="/mods/iris.jar", size=1, modified_at=T0, status="identified", project_id=IRIS,
                        version_id=version)

    async def scenario() -> tuple[list[str], list[Any]]:
        service, bundle_id = await empty_bundle()
        await service.project_add(bundle_id, ["iris"], ProjectOrigin.pack, actor=EDITOR)
        db = PterodactylDatabase()
        server = await db.server_insert(PterodactylServer(
            identifier="1a2b3c4d", internal_id=1, uuid="1a2b3c4d-0000", name="Survival", node="n1", description="",
            status=None, is_suspended=False, is_installing=False, is_transferring=False, bundle_id=bundle_id))
        await db.scan_insert(ServerScan(server_id=server.id, directory="/mods", scanned_at=day(1), full=False,  # type: ignore[arg-type]
                                        files=[jar("other-pack-version")], bundle_id="another-pack"))
        await db.scan_insert(ServerScan(server_id=server.id, directory="/mods", scanned_at=day(2), full=False,  # type: ignore[arg-type]
                                        files=[jar("this-pack-version")], bundle_id=bundle_id))
        api = service.modrinthAPI
        before = len(getattr(api, "calls", []))
        page = await service.mod_history(bundle_id, IRIS)
        return [e.text for e in page.entries], getattr(api, "calls", [])[before:]

    entries, calls = mongo(scenario)
    assert any("this-pack-version" in t for t in entries) and not any("other-pack-version" in t for t in entries)
    assert calls == []


# The suggestions (T4 of d-7985ee-16be41; user-walk N13, N19).


def stable_build(number: int, version: str, *, game: str = "1.21.1", loader: str = "fabric", via: str | None = None,
                 stable: bool = True, test: bool = False) -> Any:
    from src.library.resolution import Lock, LockEntry, ResolveRequest, Target
    from src.model.modpack import ModpackBuild
    entry = LockEntry(project_id="P", origin="pack", version_id="id-" + version, version_number=version, file=None,
                      environment=None, server="include", reason="r", requested_by=[], via=via)  # type: ignore[arg-type]
    return ModpackBuild.model_construct(
        number=number, created_at=day(number), stable=stable, test=test,
        request=ResolveRequest(target=Target(game_version=game, loader=loader), projects=[]),
        lock=Lock(entries=[entry], problems=[], suggestions=[], needs_projects=set(), needs_types=set(), needs_versions=set()))


def mod_note(verdict: str, version: str, days: int, archived: bool = False) -> Any:
    from tests.test_web_modpacks import note
    return note(verdict, "x", project_id="P", version_id="id-" + version, version_number=version, at=day(days), archived=archived)


def suggest(builds: list[Any], notes: list[Any], via: str | None = None, fits: Any = None) -> Any:
    from src.library.history import suggested_version
    return suggested_version("P", builds, notes, "1.21.1", "fabric", via, fits or (lambda version_id: True))


def test_the_suggestion_prefers_the_last_stable_build() -> None:
    found = suggest([stable_build(3, "1.6.2"), stable_build(5, "1.6.4"), stable_build(6, "1.7.2", stable=False)],
                    [mod_note("works", "1.7.2", 9)])
    assert (found.version_number, found.source, found.why) == ("1.6.4", "stable", "build 5 estable")


def test_without_stable_builds_the_last_works_note() -> None:
    found = suggest([], [mod_note("works", "1.6.2", 1), mod_note("works", "1.6.4", 2), mod_note("fails", "1.7.2", 3)])
    assert (found.version_number, found.source) == ("1.6.4", "note") and "Eddie Editor" in found.why


def test_a_later_fails_note_wins_and_archived_notes_do_not_count() -> None:
    found = suggest([], [mod_note("works", "1.6.4", 1), mod_note("fails", "1.6.4", 2), mod_note("works", "1.6.2", 1),
                         mod_note("works", "1.7.0", 5, archived=True)])
    assert found.version_number == "1.6.2"


def test_another_target_or_way_is_never_suggested() -> None:
    assert suggest([stable_build(5, "1.6.4", game="1.20.1")], []) is None
    assert suggest([stable_build(5, "1.6.4", via="connector")], []) is None
    assert suggest([], [mod_note("works", "1.6.4", 1)], fits=lambda version_id: False) is None


# The final review (2026-10-26).


@pytest.mark.network
def test_a_history_with_a_note_and_a_replaced_origin_draws_and_pages(mongo: Any) -> None:
    """Critical 1: notes and past origins come back from MongoDB without a timezone; the history mixed them
    with aware dates and failed with a 500."""
    from src.database.note import NoteDatabase
    from src.model.modpack import PinOrigin, PinRecord, PinSource, ProjectOrigin
    from src.model.modpack.note import NoteTarget
    from src.services.notes import NoteService
    from tests._actors import EDITOR, RecordingAudit
    from tests._modrinth import IRIS
    from tests.test_builds import empty_bundle

    async def scenario() -> tuple[int, int]:
        service, bundle_id = await empty_bundle()
        await service.project_add(bundle_id, ["iris"], ProjectOrigin.pack, actor=EDITOR)
        await service.build_create(bundle_id, actor=EDITOR)
        notes = NoteService(noteDB=NoteDatabase(), audit=RecordingAudit())
        await notes.create(bundle_id, NoteTarget(project_id=IRIS, version_id="v", version_number="1.0"), "fails", "x",
                           None, None, "k", EDITOR)
        db = service.modpackDB
        await db.bundle_pin_set(bundle_id, IRIS, "a")
        first = PinRecord(origins=[PinOrigin(source=PinSource.admin, version_id="a")], selected=PinSource.admin)
        await db.bundle_pin_record_set(bundle_id, IRIS, first, "a")
        read = (await db.bundle_get(bundle_id)).pin_origins[IRIS]
        await db.bundle_pin_record_set(bundle_id, IRIS, read.model_copy(update={"origins": []}), None)
        page = await service.mod_history(bundle_id, IRIS)
        older = await service.mod_history(bundle_id, IRIS, before=page.entries[0].at.replace(tzinfo=None))
        return len(page.entries), len(older.entries)

    total, older = mongo(scenario)
    assert total >= 3 and older == total - 1


def test_two_servers_with_one_name_are_two_servers() -> None:
    """Important 7: scans were grouped by the server's name."""
    scans = [ScanPoint(server="Survival", server_id=s, at=day(n), version=v)
             for n, s, v in ((1, "a", "1.6"), (2, "b", "1.7"), (3, "a", "1.6"), (4, "b", "1.7"))]
    assert texts(mod_history([], scans, [], [], [], [])) == ["Servidor Survival · 1.7", "Servidor Survival · 1.6"]
