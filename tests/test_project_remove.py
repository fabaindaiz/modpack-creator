"""Quitar un mod del pack (d-7985ee-881689): se van también su pin, su decisión de servidor y su
excepción de canal, en la misma escritura, salvo que la última resolución guardada lo traiga como
dependencia de otro mod; sin resolución guardada se conservan. El evento lo registra. Contra MongoDB
real."""
from typing import Any, Sequence

import pytest

from src.library.resolution import Problem, request_digest
from src.model.modpack import ModpackResolution, ProjectOrigin
from tests._actors import EDITOR
from tests.conftest import MongoRunner
from tests.test_mod_list import NOW, FakeModrinth, Scenario, entry, lock

pytestmark = pytest.mark.network


async def fresh(s: Scenario, bundle_id: str, *entries: Any, problems: Sequence[Problem] = ()) -> None:
    """A saved resolution of the bundle as it is now: its digest is the current request's."""
    bundle = await s.modpackDB.bundle_get(bundle_id)
    digest = request_digest(await s.service._request(bundle))
    await s.modpackDB.resolution_save(ModpackResolution(
        bundle_id=bundle.id, resolved_at=NOW, request_digest=digest,  # type: ignore[arg-type]
        lock=lock(*entries).model_copy(update={"problems": list(problems)})))


async def removed(s: Scenario, resolution: tuple[Any, ...] | None, stale: bool = False,
                  problems: Sequence[Problem] = ()) -> tuple[Any, ...]:
    """P1 and P2 in the pack, each with a pin, a decision and an exception; P1 is removed. With
    `stale`, the saved resolution is of the pack as it was before (another digest)."""
    bundle_id = await s.bundle([("P1", ProjectOrigin.admin), ("P2", ProjectOrigin.admin)],
                               pins={"P1": "v-P1-1", "P2": "v-P2-1"})
    await s.modpackDB.bundle_decisions_set(bundle_id, {"P1": True, "P2": False})
    await s.modpackDB.bundle_channel_exception_set(bundle_id, "P1", "beta")
    if resolution is not None:
        if stale:
            await s.resolved(bundle_id, *resolution)  # its digest ("d") is no request's
        else:
            await fresh(s, bundle_id, *resolution, problems=problems)
    dependents = await s.service.project_dependents(bundle_id, "P1")
    await s.service.project_remove(bundle_id, "P1", actor=EDITOR)
    bundle = await s.modpackDB.bundle_get(bundle_id)
    audit: Any = s.service.audit
    return (dependents, bundle.pins, bundle.server_decisions, bundle.channel_exceptions,
            [p.project_id for p in bundle.projects], audit.events[-1])


def test_a_mod_nobody_needs_leaves_with_its_pin_decision_and_exception(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        return await removed(Scenario(FakeModrinth()), (entry("P1", "1", origin="admin"), entry("P2", "1", origin="admin")))

    dependents, pins, decisions, exceptions, projects, event = mongo(scenario)
    assert dependents == [] and projects == ["P2"]
    assert pins == {"P2": "v-P2-1"} and decisions == {"P2": False} and exceptions == {}
    assert event[0] == "bundle.project.remove"
    assert event[2] == {"project_id": "P1", "cleared": {"pin": "v-P1-1", "server_decision": True, "channel_exception": "beta"}}


def test_a_mod_another_one_needs_keeps_them(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        return await removed(Scenario(FakeModrinth()), (entry("P1", "1", origin="admin", requested_by=["P2"]),
                                                        entry("P2", "1", origin="admin")))

    dependents, pins, decisions, exceptions, projects, event = mongo(scenario)
    assert dependents == ["P2"] and projects == ["P2"]
    assert pins == {"P1": "v-P1-1", "P2": "v-P2-1"} and decisions == {"P1": True, "P2": False}
    assert exceptions == {"P1": "beta"}
    assert event[2] == {"project_id": "P1", "cleared": {}}


def test_a_stale_saved_resolution_says_nothing_and_they_are_kept(mongo: MongoRunner) -> None:
    # Review (2026-10-12): a mod added since may need P1, and the old lock cannot say so.
    async def scenario() -> tuple[Any, ...]:
        return await removed(Scenario(FakeModrinth()), (entry("P1", "1", origin="admin"), entry("P2", "1", origin="admin")),
                             stale=True)

    dependents, pins, decisions, exceptions, projects, event = mongo(scenario)
    assert dependents is None and projects == ["P2"]
    assert pins == {"P1": "v-P1-1", "P2": "v-P2-1"} and exceptions == {"P1": "beta"}
    assert event[2] == {"project_id": "P1", "cleared": {}}


def test_a_mod_another_one_needs_but_could_not_resolve_keeps_them(mongo: MongoRunner) -> None:
    # Review (2026-10-12): an unresolved dependency is only in the lock's problems; its exception
    # may be the fix for that problem.
    async def scenario() -> tuple[Any, ...]:
        return await removed(Scenario(FakeModrinth()), (entry("P2", "1", origin="admin"),),
                             problems=[Problem(kind="unresolvable", project_id="P1", requested_by=["P2"], detail="x")])

    dependents, pins, decisions, exceptions, projects, event = mongo(scenario)
    assert dependents == ["P2"] and exceptions == {"P1": "beta"} and pins["P1"] == "v-P1-1"


def test_without_a_saved_resolution_they_are_kept(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        return await removed(Scenario(FakeModrinth()), None)

    dependents, pins, decisions, exceptions, projects, event = mongo(scenario)
    assert dependents is None and projects == ["P2"]
    assert pins == {"P1": "v-P1-1", "P2": "v-P2-1"} and exceptions == {"P1": "beta"}
    assert event[2] == {"project_id": "P1", "cleared": {}}
