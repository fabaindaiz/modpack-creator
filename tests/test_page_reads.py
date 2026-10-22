"""What a page reads, counted (review of batch B, minors 4 and 5): the dashboard counts the pending of a
pack's current resolution only, as the set-up does, reading every pack in a few queries; the pack's and
the server's pages read the pack, its build in force and its servers' scans once each. The databases
are doubles that count every call, so no MongoDB is needed."""
import asyncio
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable, Optional, cast

from beanie import PydanticObjectId

from src.library.resolution import Lock, LockEntry, request_digest
from src.model.jobs import Job
from src.model.modpack import ModpackBuild, ModpackBundle, ModpackResolution, PackProject, ProjectOrigin
from src.model.pterodactyl import PterodactylServer, ServerScan
from src.model.utils import MinecraftLoaders
from src.services.jobs import JobService
from src.services.modpack import ModpackService
from tests._actors import RecordingAudit

T0 = datetime(2026, 10, 21, tzinfo=timezone.utc)


class Counting:
    """A database double: each method is counted; `answers` gives what it returns (a value, or a
    function of its arguments), and an empty list otherwise."""

    def __init__(self, **answers: Any) -> None:
        self.answers = answers
        self.calls: Counter[str] = Counter()

    def __getattr__(self, name: str) -> Callable[..., Any]:
        if name.startswith("__"):
            raise AttributeError(name)

        async def call(*args: Any, **kwargs: Any) -> Any:
            self.calls[name] += 1
            answer = self.answers.get(name, [])
            return answer(*args) if callable(answer) else answer
        return call


class Found:
    """What `server_find()` gives: a query whose `to_list` is counted by its database."""

    def __init__(self, db: "PterodactylCounting") -> None:
        self.db = db

    async def to_list(self) -> list[PterodactylServer]:
        self.db.calls["server_find"] += 1
        return self.db.servers


class PterodactylCounting(Counting):
    def __init__(self, servers: list[PterodactylServer]) -> None:
        super().__init__(server_find_by_bundle=lambda bundle_id: [s for s in servers if s.bundle_id == bundle_id],
                         scan_latest=lambda server_id, directory: scan(server_id))
        self.servers = servers

    def server_find(self, server_ids: Optional[list[str]] = None) -> Found:
        return Found(self)


def bundle(name: str, build: Optional[int] = 3, pins: Optional[dict[str, str]] = None) -> ModpackBundle:
    return ModpackBundle.model_construct(
        id=PydanticObjectId(), name=name, description="", version="1.21.1", loader=MinecraftLoaders.fabric,
        channels=["release"], projects=[PackProject(project_id="P1", origin=ProjectOrigin.admin)],
        pins=pins or {}, pin_origins={}, server_decisions={}, channel_exceptions={}, replacements={},
        secondary_loaders=[], native_only=[], local_files=[], claimed_files=[],
        latest_build=build, recommended_build=build)


def entry(project_id: str, version_id: str) -> LockEntry:
    return LockEntry.model_construct(project_id=project_id, version_id=version_id, version_number="1",
                                     origin="admin", server="include", requested_by=[], via=None,
                                     project_type="mod")


def lock(*entries: LockEntry, undecided: int = 0) -> Lock:
    pending = [LockEntry.model_construct(project_id=f"u{n}", server="admin", origin=None, requested_by=[],
                                         version_id=f"vu{n}", version_number="1", via=None, project_type="mod")
               for n in range(undecided)]
    return Lock(entries=[*entries, *pending], problems=[], suggestions=[], needs_projects=set(), needs_types=set(),
                needs_versions=set())


def build_of(pack: ModpackBundle, number: int) -> ModpackBuild:
    return ModpackBuild.model_construct(id=PydanticObjectId(), bundle_id=pack.id, number=number, created_at=T0,
                                        lock=lock(entry("P1", f"v-{pack.name}")))


def server(name: str, pack: ModpackBundle) -> PterodactylServer:
    return PterodactylServer.model_construct(id=PydanticObjectId(), name=name, bundle_id=str(pack.id))


def scan(server_id: str) -> ServerScan:
    return ServerScan.model_construct(id=PydanticObjectId(), server_id=PydanticObjectId(server_id), directory="/mods",
                                      scanned_at=T0, full=False, files=[], drift=None)


def service(modpackDB: Counting, pterodactylDB: Counting, modrinthDB: Optional[Counting] = None,
            modrinthAPI: Optional[Counting] = None) -> ModpackService:
    return ModpackService(modpackDB=cast(Any, modpackDB), modrinthDB=cast(Any, modrinthDB or Counting()),
                          modrinthAPI=cast(Any, modrinthAPI or Counting()), loaderAPI=cast(Any, None),
                          audit=RecordingAudit(), pterodactylDB=cast(Any, pterodactylDB), jobDB=cast(Any, Counting()))


def test_the_dashboard_counts_only_current_resolutions_in_a_few_queries() -> None:
    """Minor 4: a pack changed since its resolution (the set-up's «stale») has nothing pending on the
    dashboard; the packs are read together, never one resolution per pack."""
    current, changed, never = bundle("Actual"), bundle("Cambiado"), bundle("Sin resolver", build=None)
    packs = [current, changed, never]
    builds = {(p.id, 3): build_of(p, 3) for p in (current, changed)}
    servers = [server("Uno", current), server("Dos", current), server("Tres", changed)]
    saved: dict[Any, ModpackResolution] = {}
    by_id = {p.id: p for p in packs}
    modpackDB = Counting(
        bundle_get=lambda bundle_id: by_id[PydanticObjectId(bundle_id)],
        resolution_get=lambda bundle_id: saved.get(bundle_id),
        resolution_find_many=lambda ids: [saved[i] for i in ids if i in saved],
        build_get=lambda bundle_id, number: builds[(PydanticObjectId(bundle_id), number)],
        build_find_numbers=lambda wanted: [builds[k] for k in wanted if k in builds])
    pterodactylDB = PterodactylCounting(servers)
    reader = service(modpackDB, pterodactylDB)

    async def scenario() -> tuple[dict[str, Any], dict[str, bool]]:
        digest = request_digest(await reader._request(current))
        saved[current.id] = ModpackResolution.model_construct(bundle_id=current.id, resolved_at=T0,
                                                              request_digest=digest, lock=lock(undecided=2))
        saved[changed.id] = ModpackResolution.model_construct(bundle_id=changed.id, resolved_at=T0,
                                                              request_digest="before", lock=lock(undecided=1))
        # What each pack's page says (the set-up's «stale»), one pack at a time.
        stale = {}
        for pack in (current, changed):
            found = await reader.resolution(str(pack.id))
            assert found is not None
            stale[str(pack.id)] = found[1]
        modpackDB.calls.clear()
        pterodactylDB.calls.clear()
        return await reader.current_resolutions(packs), stale

    found, stale = asyncio.run(scenario())
    assert stale == {str(current.id): False, str(changed.id): True}
    assert set(found) == {str(current.id)}  # the same answer as each page's
    assert len(found[str(current.id)].lock.undecided) == 2
    # One query each for every pack: resolutions, replacement rules, builds in force, servers; then
    # the latest scan of each server linked to a resolved pack.
    assert modpackDB.calls == Counter(resolution_find_many=1, replacement_find=1, build_find_numbers=1)
    assert pterodactylDB.calls == Counter(server_find=1, scan_latest=3)


def test_the_dashboard_reads_nothing_more_without_resolutions() -> None:
    modpackDB, pterodactylDB = Counting(resolution_find_many=[]), PterodactylCounting([])
    assert asyncio.run(service(modpackDB, pterodactylDB).current_resolutions([bundle("Pack")])) == {}
    assert modpackDB.calls == Counter(resolution_find_many=1) and not pterodactylDB.calls


def test_the_pack_s_mod_list_reads_the_pack_its_build_and_its_servers_once() -> None:
    """Minor 5: the list read the pack and the floors (build in force, servers, scans) again for the
    resolution's staleness and again for the pins."""
    pack = bundle("Pack", pins={"P1": "v-old"})
    built = build_of(pack, 3)
    saved = ModpackResolution.model_construct(bundle_id=pack.id, resolved_at=T0, request_digest="d", lock=lock())
    modpackDB = Counting(bundle_get=pack, resolution_get=saved, build_get=built)
    pterodactylDB = PterodactylCounting([server("Uno", pack), server("Dos", pack)])
    reader = service(modpackDB, pterodactylDB)
    listed = asyncio.run(reader.mod_list(str(pack.id), None))
    assert listed.resolution is saved and listed.stale
    assert modpackDB.calls["bundle_get"] == 1
    assert modpackDB.calls["build_get"] == 1
    assert modpackDB.calls["resolution_get"] == 1
    assert modpackDB.calls["replacement_find"] == 1
    assert pterodactylDB.calls["server_find_by_bundle"] == 1
    assert pterodactylDB.calls["scan_latest"] == 2


def test_the_mod_list_takes_the_pack_its_page_already_read() -> None:
    pack = bundle("Pack")
    modpackDB = Counting(bundle_get=pack, resolution_get=None, build_get=build_of(pack, 3))
    asyncio.run(service(modpackDB, PterodactylCounting([])).mod_list(str(pack.id), None, bundle=pack))
    assert modpackDB.calls["bundle_get"] == 0


def update(build: int, status: str, server_id: str = "s1") -> Job:
    return Job.model_construct(id=PydanticObjectId(), kind="server_update", key=f"server:{server_id}",
                               payload={"server_id": server_id, "build": build, "dry_run": False}, status=status,
                               checkpoint={"backup": "b1"}, created_at=T0)


def test_the_server_s_page_reads_its_last_real_update_once() -> None:
    """Minor 5: the installed build and the rollback on offer both come from the server's last real
    update, read once for the two."""
    for last in (update(4, "done"), update(5, "failed"), None):
        jobDB = Counting(last_real_update=last, rollbacks_of=[])
        jobs = JobService(jobDB=cast(Any, jobDB), pterodactylService=cast(Any, None), pterodactylDB=cast(Any, None),
                          audit=RecordingAudit())
        state = asyncio.run(jobs.update_state("s1"))
        assert jobDB.calls["last_real_update"] == 1
        assert state.installed == asyncio.run(jobs.installed_build("s1"))
        alone = asyncio.run(jobs.rollback_state("s1"))
        assert (state.rollback.allowed if state.rollback else None) == (alone.allowed if alone else None)


def test_the_pack_s_linked_servers_simulations_are_read_together() -> None:
    """Minor 5: the pack's set-up read each linked server's simulations apart."""
    jobDB = Counting(last_done_update_builds={"s1": 3, "s2": 1, "s4": "not a number"})
    jobs = JobService(jobDB=cast(Any, jobDB), pterodactylService=cast(Any, None), pterodactylDB=cast(Any, None),
                      audit=RecordingAudit())
    assert asyncio.run(jobs.simulated_builds(["s1", "s2", "s3", "s4"], "b1")) == {"s1": 3, "s2": 1}
    assert jobDB.calls == Counter(last_done_update_builds=1)


def test_an_import_s_pending_are_those_its_server_s_pack_has_not_decided() -> None:
    """Review of release 0.3.0: the finished import's row counts its pending when it is read, by the
    rule of the server's page: a jar confirmed as a project or kept as local leaves the list."""
    from src.library.scan import ClaimedFile, LocalFile
    from src.model.pterodactyl import ImportCandidate, ImportResult
    from src.services.pterodactyl import PterodactylService

    pack = bundle("Pack").model_copy(update={
        "local_files": [LocalFile.model_construct(sha512="s1")], "claimed_files": [ClaimedFile.model_construct(sha512="s2")]})
    linked = server("Uno", pack)
    result = ImportResult(added=[], local=[], too_large=[], candidates=[
        ImportCandidate(path=f"/mods/{s}.jar", sha512=s, metadata=None, candidates=[]) for s in ("s1", "s2", "s3")])

    def reader(linked_to: Optional[str]) -> PterodactylService:
        return PterodactylService(
            pterodactylAPI=cast(Any, None), modrinthAPI=cast(Any, None), modrinthDB=cast(Any, None),
            PterodactylDB=cast(Any, Counting(server_get=linked.model_copy(update={"bundle_id": linked_to}))),
            modpackDB=cast(Any, Counting(bundle_get=pack)), audit=RecordingAudit(), jobDB=cast(Any, Counting()))

    left = asyncio.run(reader(str(pack.id)).import_undecided(str(linked.id), result))
    assert [c.sha512 for c in left.candidates] == ["s3"]
    unlinked = asyncio.run(reader(None).import_undecided(str(linked.id), result))
    assert [c.sha512 for c in unlinked.candidates] == ["s1", "s2", "s3"]


def test_a_resolution_for_a_pack_already_read_does_not_read_it_again() -> None:
    """The server's page has the pack; its set-up asks whether the resolution is current."""
    pack = bundle("Pack")
    saved = ModpackResolution.model_construct(bundle_id=pack.id, resolved_at=T0, request_digest="d", lock=lock())
    modpackDB = Counting(bundle_get=pack, resolution_get=saved, build_get=build_of(pack, 3))
    found = asyncio.run(service(modpackDB, PterodactylCounting([])).resolution(str(pack.id), bundle=pack))
    assert found is not None and found[0] is saved
    assert modpackDB.calls["bundle_get"] == 0
