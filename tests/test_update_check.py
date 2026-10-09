"""La revisión de actualizaciones (N5): el build recomendado contra Modrinth, lo guardado y el aviso."""
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence, cast

import pytest
from beanie import PydanticObjectId

from src.database.jobs import JobDatabase
from src.database.modpack import ModpackDatabase
from src.database.modrinth import ModrinthDatabase
from src.database.update_check import UpdateCheckDatabase
from src.exceptions import HTTP_404_NOT_FOUND, HTTP_409_CONFLICT
from src.library.resolution import CandidateFile, Lock, LockEntry, ResolveRequest, Target
from src.model.modpack import ModpackBuild, ModpackBundle
from src.model.modrinth import ModrinthVersion
from src.model.modpack.update_check import AvailableUpdate, UpdateCheck
from src.model.utils import MinecraftLoaders
from src.services.jobs import JobContext, JobWorker
from src.services.update_check import UpdateCheckService, digest_of
from tests._actors import EDITOR, RecordingAudit
from tests.conftest import MongoRunner


def version_doc(version_id: str, project_id: str, number: str) -> ModrinthVersion:
    return ModrinthVersion.parse(
        id=version_id, project_id=project_id, author_id="a", name=number, version_number=number,
        version_type="release", status="listed", downloads=1, game_versions=["26.3"], loaders=["fabric"],
        featured=False, date_published="2020-01-01T00:00:00Z", dependencies=[], files=[], changelog=None)


def entry(project_id: str, version_id: str, number: str, project_type: str = "mod") -> LockEntry:
    return LockEntry(project_id=project_id, origin="pack", project_type=project_type, version_id=version_id,
                     version_number=number, environment="client_and_server", server="include",
                     reason="en el pack", requested_by=[],
                     file=CandidateFile(filename=f"{project_id}.jar", url="https://cdn/x", sha1="s1",
                                        sha512=f"h-{project_id}", size=1))


class FakeModrinth:
    """`POST /version_files/update`: el mejor candidato por hash, o nada; guarda lo que recibe."""

    def __init__(self, latest: dict[str, ModrinthVersion], failing: bool = False) -> None:
        self.latest = latest
        self.failing = failing
        self.calls: list[dict[str, Any]] = []

    async def files_update_hash_post_many(self, hashes: list[str], algorithm: str = "sha512", loaders: Sequence[str] = (),
                                          game_versions: Sequence[str] = (), version_types: Sequence[str] = ()) -> dict[str, ModrinthVersion]:
        self.calls.append({"hashes": sorted(hashes), "loaders": sorted(loaders), "game_versions": list(game_versions),
                           "version_types": list(version_types)})
        if self.failing:
            raise RuntimeError("Modrinth down")
        return {h: self.latest[h] for h in hashes if h in self.latest}


class Recorder:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def notify(self, text: str) -> None:
        self.sent.append(text)

    def handlers(self) -> dict[str, Any]:
        return {}


def make(api: FakeModrinth, audit: RecordingAudit | None = None, notifier: Recorder | None = None) -> UpdateCheckService:
    return UpdateCheckService(ModpackDatabase(), cast(Any, api), ModrinthDatabase(), UpdateCheckDatabase(), JobDatabase(),
                              audit or RecordingAudit(), notifier or Recorder())


async def bundle_with(entries: list[LockEntry], pins: dict[str, str] | None = None,
                      channels: list[str] | None = None, recommended: int | None = 1) -> str:
    bundle = await ModpackDatabase().bundle_insert(ModpackBundle(
        name="Survival", description="", version="26.3", loader=MinecraftLoaders.fabric,
        pins=pins or {}, channels=channels or ["release", "beta"], latest_build=1, recommended_build=recommended))
    await ModpackBuild.insert(ModpackBuild(
        bundle_id=PydanticObjectId(str(bundle.id)), number=1, created_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        request=ResolveRequest(target=Target(game_version="26.3", loader="fabric"), projects=[]),
        lock=Lock(entries=entries, problems=[], suggestions=[], needs_projects=set(), needs_types=set(),
                  needs_versions=set())))
    return str(bundle.id)


def three_mods() -> list[LockEntry]:
    return [entry("A", "a1", "1.0"), entry("B", "b1", "1.0"), entry("C", "c1", "1.0")]


def newer() -> dict[str, ModrinthVersion]:
    # A tiene una nueva, B ya es la última, C (fijado) también tiene una más nueva.
    return {"h-A": version_doc("a2", "A", "2.0"), "h-B": version_doc("b1", "B", "1.0"),
            "h-C": version_doc("c2", "C", "3.0")}


@pytest.mark.network
def test_a_build_of_three_mods_with_one_current_and_one_pinned_gives_two_updates(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[UpdateCheck, FakeModrinth]:
        api = FakeModrinth(newer())
        bundle_id = await bundle_with(three_mods(), pins={"C": "c1"})
        return await make(api).compute(bundle_id), api

    check, api = mongo(scenario)
    assert check.build_number == 1 and check.error is None
    assert check.updates == [AvailableUpdate(project_id="A", from_version="1.0", to_version="2.0", pinned=False, title="A",
                                            from_version_id="a1", to_version_id="a2"),
                             AvailableUpdate(project_id="C", from_version="1.0", to_version="3.0", pinned=True, title="C",
                                            from_version_id="c1", to_version_id="c2")]
    # Un solo grupo de loaders (los mods), con la versión del juego y los canales del bundle.
    assert api.calls == [{"hashes": ["h-A", "h-B", "h-C"], "loaders": ["fabric"], "game_versions": ["26.3"],
                          "version_types": ["release", "beta"]}]


@pytest.mark.network
def test_mods_shaders_and_resource_packs_are_asked_in_separate_calls(mongo: MongoRunner) -> None:
    async def scenario() -> FakeModrinth:
        api = FakeModrinth({})
        bundle_id = await bundle_with([entry("A", "a1", "1", "mod"), entry("S", "s1", "1", "shader"),
                                       entry("R", "r1", "1", "resourcepack")])
        await make(api).compute(bundle_id)
        return api

    api = mongo(scenario)
    by_loaders = {tuple(c["loaders"]): c["hashes"] for c in api.calls}
    assert by_loaders == {("fabric",): ["h-A"], ("canvas", "iris", "optifine", "vanilla"): ["h-S"],
                          ("minecraft",): ["h-R"]}


@pytest.mark.network
def test_a_bundle_without_a_recommended_build_checks_the_latest(mongo: MongoRunner) -> None:
    async def scenario() -> UpdateCheck:
        bundle_id = await bundle_with(three_mods(), recommended=None)
        return await make(FakeModrinth(newer())).compute(bundle_id)

    assert mongo(scenario).build_number == 1


def test_the_digest_ignores_pinned_updates_and_order() -> None:
    a = AvailableUpdate(project_id="A", from_version="1", to_version="2", pinned=False)
    b = AvailableUpdate(project_id="B", from_version="1", to_version="2", pinned=False)
    pinned = AvailableUpdate(project_id="C", from_version="1", to_version="2", pinned=True)
    assert digest_of([a, b, pinned]) == digest_of([b, a]) != digest_of([a])


@pytest.mark.network
def test_the_same_updates_are_notified_once_and_different_ones_again(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[list[str], list[str], list[str]]:
        api, notifier = FakeModrinth(newer()), Recorder()
        service = make(api, notifier=notifier)
        bundle_id = await bundle_with(three_mods(), pins={"C": "c1"})
        worker = JobWorker(JobDatabase(), service.handlers())
        for _ in range(2):
            await service.enqueue(bundle_id, None)
            assert await worker.run_once()
        after_two = list(notifier.sent)
        api.latest["h-B"] = version_doc("b2", "B", "1.1")
        await service.enqueue(bundle_id, None)
        await worker.run_once()
        return after_two, list(notifier.sent), [str(c.updates) for c in [await service.latest(bundle_id)] if c]  # type: ignore[misc]

    after_two, after_three, _ = mongo(scenario)
    assert after_two == ["🔎 Survival (build 1): 1 mods con actualización — A 1.0→2.0"]
    assert len(after_three) == 2 and "2 mods con actualización — A 1.0→2.0, B 1.0→1.1" in after_three[1]


@pytest.mark.network
def test_only_pinned_updates_notify_nothing_and_the_text_is_cut_to_2000(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[list[str], list[str]]:
        only_pinned, notifier = Recorder(), Recorder()
        bundle_id = await bundle_with(three_mods(), pins={"A": "a1", "B": "b1", "C": "c1"})
        service = make(FakeModrinth({"h-A": version_doc("a2", "A", "2"), "h-C": version_doc("c2", "C", "3")}), notifier=only_pinned)
        await service.enqueue(bundle_id, None)
        await JobWorker(JobDatabase(), service.handlers()).run_once()
        many = [entry(f"P{i:03}", f"v{i}", "1.0") for i in range(300)]
        big = await bundle_with(many)
        service = make(FakeModrinth({f"h-P{i:03}": version_doc(f"w{i}", f"P{i:03}", "2.0") for i in range(300)}), notifier=notifier)
        await service.enqueue(big, None)
        await JobWorker(JobDatabase(), service.handlers()).run_once()
        return only_pinned.sent, notifier.sent

    only_pinned, long = mongo(scenario)
    assert only_pinned == []
    assert len(long) == 1 and len(long[0]) == 2000 and "300 mods" in long[0]


@pytest.mark.network
def test_modrinth_down_stores_an_error_fails_the_job_and_sends_nothing(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, Any, list[str]]:
        notifier = Recorder()
        service = make(FakeModrinth({}, failing=True), notifier=notifier)
        bundle_id = await bundle_with(three_mods())
        job = await service.enqueue(bundle_id, None)
        await JobWorker(JobDatabase(), service.handlers()).run_once()
        return await JobDatabase().get(str(job.id)), await service.latest(bundle_id), notifier.sent

    job, check, sent = mongo(scenario)
    assert (job.status, job.active) == ("failed", False)
    assert check is not None and check.updates == [] and "Modrinth down" in (check.error or "")
    assert sent == []


class FlakyChecks(UpdateCheckDatabase):
    """MongoDB failing once, where named: `last_notified` (what _should_notify reads) or the first
    `insert` (the result's)."""

    def __init__(self, where: str) -> None:
        self.where = where

    async def last_notified(self, bundle_id: PydanticObjectId) -> UpdateCheck | None:
        if self.where == "last_notified":
            self.where = ""
            raise RuntimeError("mongo hiccup")
        return await super().last_notified(bundle_id)

    async def insert(self, check: UpdateCheck) -> UpdateCheck:
        if self.where == "insert":
            self.where = ""
            raise RuntimeError("mongo hiccup")
        return await super().insert(check)


class BundleUnreadableAfterCompute(ModpackDatabase):
    """Reads the bundle for `enqueue` and `compute`, then fails: the read of the error branch."""

    def __init__(self) -> None:
        self.reads = 0

    async def bundle_get(self, bundle_id: str) -> ModpackBundle:
        self.reads += 1
        if self.reads > 2:
            raise RuntimeError("mongo hiccup")
        return await super().bundle_get(bundle_id)


@pytest.mark.network
@pytest.mark.parametrize("where", ["last_notified", "insert", "bundle_get"])
def test_a_failure_after_the_compute_still_stores_an_error_check(mongo: MongoRunner, where: str) -> None:
    """Whatever fails in the step (Modrinth, or MongoDB reading the last notice, storing the result
    or reading the bundle again), a failed check is stored: the scheduler counts it as today's and
    does not queue the modpack again every 5 minutes."""
    async def scenario() -> tuple[Any, Any]:
        modpacks = BundleUnreadableAfterCompute() if where == "bundle_get" else ModpackDatabase()
        api = FakeModrinth({}, failing=where == "bundle_get") if where == "bundle_get" else FakeModrinth(newer())
        checks = FlakyChecks(where) if where != "bundle_get" else UpdateCheckDatabase()
        service = UpdateCheckService(modpacks, cast(Any, api), ModrinthDatabase(), checks, JobDatabase(),
                                     RecordingAudit(), Recorder())
        bundle_id = await bundle_with(three_mods())
        job = await service.enqueue(bundle_id, None)
        await JobWorker(JobDatabase(), service.handlers()).run_once()
        return await JobDatabase().get(str(job.id)), await UpdateCheckDatabase().latest(PydanticObjectId(bundle_id))

    job, check = mongo(scenario)
    assert job.status == "failed"
    assert check is not None and check.error is not None and check.notified_digest is None
    assert ("Modrinth down" if where == "bundle_get" else "mongo hiccup") in check.error
    assert check.build_number == (0 if where == "bundle_get" else 1)


@pytest.mark.network
def test_a_second_check_of_the_same_modpack_gives_a_readable_409(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, Any]:
        audit = RecordingAudit()
        service = make(FakeModrinth({}), audit=audit)
        bundle_id = await bundle_with(three_mods())
        first = await service.enqueue(bundle_id, EDITOR)
        with pytest.raises(HTTP_409_CONFLICT) as error:
            await service.enqueue(bundle_id, EDITOR)
        return first, (error.value.detail, audit.events)

    first, (detail, events) = mongo(scenario)
    assert first.key.startswith("bundle:") and first.requested_by.sub == EDITOR.sub
    assert "Ya hay una revisión en curso" in str(detail)
    assert [e[0] for e in events] == ["bundle.update_check"]


@pytest.mark.network
def test_a_modpack_without_a_build_is_refused_in_words_before_queueing(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, Any, Any]:
        audit = RecordingAudit()
        service = make(FakeModrinth({}), audit=audit)
        bundle = await ModpackDatabase().bundle_insert(ModpackBundle(
            name="Sin build", description="", version="26.3", loader=MinecraftLoaders.fabric))
        with pytest.raises(HTTP_409_CONFLICT) as error:
            await service.enqueue(str(bundle.id), EDITOR)
        return error.value.detail, audit.events, await JobDatabase().recent(10)

    detail, events, jobs = mongo(scenario)
    assert detail == "Este modpack todavía no tiene build: crea uno para poder buscar actualizaciones."
    assert events == [] and jobs == []


@pytest.mark.network
def test_latest_many_returns_the_newest_check_of_each_bundle(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        db = UpdateCheckDatabase()
        a, b = await bundle_with([]), await bundle_with([])
        from beanie import PydanticObjectId
        for bundle, day in ((a, 1), (a, 2), (b, 1)):
            await db.insert(UpdateCheck(bundle_id=PydanticObjectId(bundle), build_number=day,
                                        checked_at=datetime(2026, 10, day, tzinfo=timezone.utc), updates=[]))
        found = await db.latest_many([PydanticObjectId(a), PydanticObjectId(b)])
        return {str(k): v.build_number for k, v in found.items()}, a, b

    found, a, b = mongo(scenario)
    assert found == {a: 2, b: 1}


@pytest.mark.network
def test_the_notice_names_mods_by_title_and_falls_back_to_the_id(mongo: MongoRunner) -> None:
    async def scenario() -> list[str]:
        from tests._modrinth import RecordedModrinthAPI
        [sodium] = await RecordedModrinthAPI().project_get_many(["AANobbMI"])
        sodium.project_id, sodium.title = "A", "Sodium"
        await ModrinthDatabase().project_upsert_many([sodium])
        notifier = Recorder()
        service = make(FakeModrinth(newer()), notifier=notifier)
        bundle_id = await bundle_with(three_mods())
        await service.enqueue(bundle_id, None)
        await JobWorker(JobDatabase(), service.handlers()).run_once()
        return notifier.sent

    [text] = mongo(scenario)
    assert "Sodium 1.0→2.0" in text and "C 1.0→3.0" in text


@pytest.mark.network
def test_a_hash_answered_with_another_project_is_not_an_update(mongo: MongoRunner) -> None:
    async def scenario() -> UpdateCheck:
        bundle_id = await bundle_with([entry("A", "a1", "1.0")])
        return await make(FakeModrinth({"h-A": version_doc("x2", "OTHER", "2.0")})).compute(bundle_id)

    assert mongo(scenario).updates == []


@pytest.mark.network
def test_rerunning_the_step_after_the_insert_does_not_notify_again(mongo: MongoRunner) -> None:
    async def scenario() -> list[str]:
        notifier = Recorder()
        service = make(FakeModrinth(newer()), notifier=notifier)
        bundle_id = await bundle_with(three_mods())
        await service.enqueue(bundle_id, None)
        claimed = await JobDatabase().claim("w", datetime.now(timezone.utc), timedelta(seconds=60))
        assert claimed is not None
        context = JobContext(claimed, JobDatabase(), "w")
        await service._run(claimed, context)
        await service._run(claimed, context)  # the checkpoint holds: the step does not run twice
        # And a retry that lost the checkpoint (crash after the insert) finds the digest stored.
        claimed.checkpoint.clear()
        await service._run(claimed, context)
        return notifier.sent

    assert len(mongo(scenario)) == 1


@pytest.mark.network
def test_latest_with_a_malformed_id_is_a_404(mongo: MongoRunner) -> None:
    async def scenario() -> None:
        await make(FakeModrinth({})).latest("not-an-id")

    with pytest.raises(HTTP_404_NOT_FOUND):
        mongo(scenario)


@pytest.mark.network
def test_latest_many_with_several_checks_per_bundle_returns_only_the_newest(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        db = UpdateCheckDatabase()
        ids = [PydanticObjectId(await bundle_with([])) for _ in range(3)]
        for bundle in ids[:2]:
            for day in (3, 1, 5, 2):
                await db.insert(UpdateCheck(bundle_id=bundle, build_number=day,
                                            checked_at=datetime(2026, 10, day, tzinfo=timezone.utc)))
        found = await db.latest_many(ids)
        return {k: v.build_number for k, v in found.items()}, ids

    found, ids = mongo(scenario)
    assert found == {ids[0]: 5, ids[1]: 5}
