"""Acciones de editor: cada cambio que hace un editor queda en la auditoría, después de tener éxito.

- Una tabla de métodos: cada uno graba exactamente un evento con su acción, su tipo de objetivo
  y la persona que lo pidió.
- Si grabar falla, el error se propaga y la acción queda hecha (decidido: el 500 lleva `request_id`).
- El programador y el handler del worker actúan sin persona: `actor=None` explícito, ningún evento.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, cast

import pytest

from src.database.jobs import JobDatabase
from src.database.modpack import ModpackDatabase
from src.database.modrinth import ModrinthDatabase
from src.database.pterodactyl import PterodactylDatabase
from src.model.modpack import ModpackBundle, ModpackBundlePatch, ProjectOrigin
from src.model.pterodactyl import PterodactylServer
from src.model.users import Actor
from src.model.utils import MinecraftLoaders
from src.services.jobs import JobService, JobWorker
from src.services.modpack import ModpackService
from src.services.pterodactyl import PterodactylService
from tests._actors import EDITOR, OPERATOR, RecordingAudit
from tests._modrinth import RecordedModrinthAPI
from tests.conftest import MongoRunner
from tests.test_builds import FakeLoaders
from tests.test_server_scan import FakePanel

pytestmark = pytest.mark.network

T0 = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)

Case = Callable[[RecordingAudit, Actor], Awaitable[str]]
"""Prepares what it needs without recording, makes the call, and returns the target id."""


class Panel(FakePanel):
    async def server_get(self, uuid: str) -> PterodactylServer:
        return server_model(uuid)


def server_model(uuid: str) -> PterodactylServer:
    return PterodactylServer(identifier="1a2b3c4d", internal_id=1, uuid=uuid, name="Survival", node="n1",
                             description="", status=None, is_suspended=False, is_installing=False,
                             is_transferring=False)


def modpack(audit: RecordingAudit) -> ModpackService:
    return ModpackService(modpackDB=ModpackDatabase(), modrinthDB=ModrinthDatabase(),
                          modrinthAPI=cast(Any, RecordedModrinthAPI()), loaderAPI=cast(Any, FakeLoaders()),
                          audit=audit)


def pterodactyl(audit: RecordingAudit) -> PterodactylService:
    return PterodactylService(pterodactylAPI=cast(Any, Panel()), PterodactylDB=PterodactylDatabase(),
                              modrinthAPI=cast(Any, RecordedModrinthAPI()), modrinthDB=ModrinthDatabase(),
                              modpackDB=ModpackDatabase(), audit=audit)


async def bundle_of(service: ModpackService, audit: RecordingAudit, *slugs: str) -> str:
    created = await service.bundle_create(ModpackBundle(
        name="pack", description="", version="26.3", loader=MinecraftLoaders.fabric), actor=EDITOR)
    if slugs:
        await service.project_add(str(created.id), list(slugs), ProjectOrigin.pack, actor=EDITOR)
    audit.events.clear()
    return str(created.id)


async def stored_server(uuid: str = "1a2b3c4d-0000") -> str:
    return str((await PterodactylDatabase().server_insert(server_model(uuid))).id)


async def bundle_create(audit: RecordingAudit, actor: Actor) -> str:
    service = modpack(audit)
    created = await service.bundle_create(ModpackBundle(
        name="pack", description="", version="26.3", loader=MinecraftLoaders.fabric), actor=actor)
    return str(created.id)


async def bundle_update(audit: RecordingAudit, actor: Actor) -> str:
    service = modpack(audit)
    bundle_id = await bundle_of(service, audit)
    await service.bundle_update(bundle_id, ModpackBundlePatch(name="renamed"), actor=actor)
    return bundle_id


async def decision_set(audit: RecordingAudit, actor: Actor) -> str:
    service = modpack(audit)
    bundle_id = await bundle_of(service, audit)
    await service.decision_set(bundle_id, "AANobbMI", True, actor=actor)
    return bundle_id


async def decisions_set(audit: RecordingAudit, actor: Actor) -> str:
    service = modpack(audit)
    bundle_id = await bundle_of(service, audit)
    await service.decisions_set(bundle_id, {"AANobbMI": True, "P2": False}, actor=actor)
    return bundle_id


async def pin_set(audit: RecordingAudit, actor: Actor) -> str:
    service = modpack(audit)
    bundle_id = await bundle_of(service, audit, "sodium")
    await service.pin_set(bundle_id, "AANobbMI", "v4PSXean", actor=actor)
    return bundle_id


async def pin_unset(audit: RecordingAudit, actor: Actor) -> str:
    service = modpack(audit)
    bundle_id = await bundle_of(service, audit, "sodium")
    await service.pin_unset(bundle_id, "AANobbMI", actor=actor)
    return bundle_id


async def project_add(audit: RecordingAudit, actor: Actor) -> str:
    service = modpack(audit)
    bundle_id = await bundle_of(service, audit)
    await service.project_add(bundle_id, ["iris"], ProjectOrigin.admin, actor=actor)
    return bundle_id


async def pack_replace(audit: RecordingAudit, actor: Actor) -> str:
    service = modpack(audit)
    bundle_id = await bundle_of(service, audit, "iris")
    await service.pack_replace(bundle_id, ["sodium"], actor=actor)
    return bundle_id


async def project_remove(audit: RecordingAudit, actor: Actor) -> str:
    service = modpack(audit)
    bundle_id = await bundle_of(service, audit, "iris")
    [project] = (await service.project_find(bundle_id))
    await service.project_remove(bundle_id, project.project_id, actor=actor)
    return bundle_id


async def build_create(audit: RecordingAudit, actor: Actor) -> str:
    service = modpack(audit)
    bundle_id = await bundle_of(service, audit, "iris")
    await service.build_create(bundle_id, actor=actor)
    return bundle_id


async def build_recommend(audit: RecordingAudit, actor: Actor) -> str:
    service = modpack(audit)
    bundle_id = await bundle_of(service, audit, "iris")
    await service.build_create(bundle_id, actor=EDITOR)
    audit.events.clear()
    await service.build_recommend(bundle_id, 1, actor=actor)
    return bundle_id


async def replacement_set(audit: RecordingAudit, actor: Actor) -> str:
    await modpack(audit).replacement_set("AANobbMI", "embeddium", actor=actor)
    return "AANobbMI"


async def replacement_delete(audit: RecordingAudit, actor: Actor) -> str:
    service = modpack(audit)
    await service.replacement_set("AANobbMI", None, actor=EDITOR)
    audit.events.clear()
    await service.replacement_delete("AANobbMI", actor=actor)
    return "AANobbMI"


async def server_create(audit: RecordingAudit, actor: Actor) -> str:
    return str((await pterodactyl(audit).server_create("1a2b3c4d-0000", actor=actor)).id)


async def server_update(audit: RecordingAudit, actor: Actor) -> str:
    server_id = await stored_server()
    await pterodactyl(audit).server_update(server_id, actor=actor)
    return server_id


async def server_scan(audit: RecordingAudit, actor: Actor) -> str:
    server_id = await stored_server()
    await pterodactyl(audit).scan(server_id, actor=actor)
    return server_id


async def server_scan_queue(audit: RecordingAudit, actor: Actor) -> str:
    server_id = await stored_server()
    jobs = JobService(jobDB=JobDatabase(), pterodactylService=cast(Any, None), pterodactylDB=PterodactylDatabase(),
                      audit=audit)
    job = await jobs.enqueue_scan(server_id, actor=actor, full=True)
    assert job.requested_by == actor.ref()
    return server_id


TABLE: list[tuple[Case, str, str, dict[str, Any] | None]] = [
    (bundle_create, "bundle.create", "bundle", None),
    (bundle_update, "bundle.update", "bundle", None),
    (decision_set, "bundle.decision", "bundle", {"project_id": "AANobbMI", "server": True}),
    (decisions_set, "bundle.decision", "bundle", {"decisions": {"AANobbMI": True, "P2": False}}),
    (pin_set, "bundle.pin", "bundle", {"project_id": "AANobbMI", "version_id": "v4PSXean"}),
    (pin_unset, "bundle.pin", "bundle", {"project_id": "AANobbMI", "version_id": None}),
    (project_add, "bundle.project.add", "bundle", {"refs": ["iris"], "origin": "admin"}),
    (pack_replace, "bundle.project.replace", "bundle", None),
    (project_remove, "bundle.project.remove", "bundle", None),
    (build_create, "build.create", "bundle", {"number": 1}),
    (build_recommend, "build.recommend", "bundle", {"number": 1}),
    (replacement_set, "replacement.set", "replacement", None),
    (replacement_delete, "replacement.delete", "replacement", None),
    (server_create, "server.create", "server", None),
    (server_update, "server.refresh", "server", None),
    (server_scan, "server.scan", "server", None),
    (server_scan_queue, "server.scan.queue", "server", None),
]


@pytest.mark.parametrize("case,action,kind,details", TABLE, ids=[row[1] for row in TABLE])
def test_each_editor_action_records_one_event(mongo: MongoRunner, case: Case, action: str, kind: str,
                                              details: dict[str, Any] | None) -> None:
    async def scenario() -> tuple[str, RecordingAudit]:
        audit = RecordingAudit()
        target_id = await case(audit, EDITOR)
        return target_id, audit

    target_id, audit = mongo(scenario)
    [(recorded, target, recorded_details, _, who)] = audit.events
    assert (recorded, target.kind, target.id, who) == (action, kind, target_id, EDITOR.ref())
    if details is not None:
        assert recorded_details == details


def test_an_operator_may_do_the_same(mongo: MongoRunner) -> None:
    async def scenario() -> list[str]:
        audit = RecordingAudit()
        await bundle_create(audit, OPERATOR)
        await server_scan(audit, OPERATOR)
        return [event[4].sub for event in audit.events]

    assert mongo(scenario) == [OPERATOR.sub, OPERATOR.sub]


def test_a_failed_record_propagates_after_the_change_is_done(mongo: MongoRunner) -> None:
    async def scenario() -> int:
        with pytest.raises(RuntimeError):
            await bundle_create(RecordingAudit(fail=True), EDITOR)
        return len(await ModpackDatabase().bundle_find().to_list())

    assert mongo(scenario) == 1


def test_a_refused_change_is_not_recorded(mongo: MongoRunner) -> None:
    from fastapi import HTTPException

    async def scenario() -> int:
        audit = RecordingAudit()
        service = modpack(audit)
        bundle_id = await bundle_of(service, audit)
        with pytest.raises(HTTPException):
            await service.project_add(bundle_id, ["no-such-mod"], ProjectOrigin.pack, actor=EDITOR)
        with pytest.raises(HTTPException):
            await service.replacement_delete("nothing", actor=EDITOR)
        return len(audit.events)

    assert mongo(scenario) == 0


class FakeScans:
    def __init__(self) -> None:
        self.actors: list[Any] = []

    async def scan(self, server_id: str, actor: Any, full: bool = False) -> Any:
        self.actors.append(actor)
        return type("Scan", (), {"id": "scan-1", "files": []})()

    async def drift(self, server_id: str) -> None:
        return None


def test_the_scheduler_and_the_worker_scan_without_a_person(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[int, int, list[Any], Any]:
        audit = RecordingAudit()
        await stored_server()
        scans = FakeScans()
        jobs = JobService(jobDB=JobDatabase(), pterodactylService=cast(Any, scans), pterodactylDB=PterodactylDatabase(),
                          audit=audit)
        queued = await jobs.schedule_scans(T0, every=timedelta(hours=6), full_every=timedelta(days=7))
        await JobWorker(JobDatabase(), jobs.handlers(), owner="w1", clock=lambda: T0).run_once()
        return len(queued), len(audit.events), scans.actors, queued[0].requested_by

    assert mongo(scenario) == (1, 0, [None], None)


def test_bulk_decisions_capped_at_500() -> None:
    """More than 500 decisions at once is a 422 before anything is read or written."""
    from fastapi import HTTPException

    async def scenario() -> tuple[int, str]:
        service = ModpackService(modpackDB=cast(Any, None), modrinthDB=cast(Any, None), modrinthAPI=cast(Any, None),
                                 loaderAPI=cast(Any, None), audit=RecordingAudit())
        with pytest.raises(HTTPException) as refused:
            await service.decisions_set("b", {f"P{i}": True for i in range(501)}, actor=EDITOR)
        return refused.value.status_code, str(refused.value.detail)

    import asyncio
    code, detail = asyncio.run(scenario())
    assert code == 422 and "Demasiadas decisiones de una vez (máximo 500)" in detail
