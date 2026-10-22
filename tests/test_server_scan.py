"""Escanear un servidor y compararlo con un build (i-7985ee-43df2f opción 1, i-7985ee-4364a3).

El servicio lista `mods/`, baja solo los jars nuevos o cambiados, los identifica por sha1 en
Modrinth y guarda el escaneo. Nada de esto escribe en el servidor. Contra MongoDB real; el panel
es un doble, y Modrinth contesta con versiones reales grabadas.
"""
import copy
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, cast

import pytest
from fastapi import HTTPException

from src.database.modpack import ModpackDatabase
from src.database.modrinth import ModrinthDatabase
from src.database.pterodactyl import PterodactylDatabase
from src.model.modpack import ModpackBundle, ProjectOrigin
from src.model.modrinth import ModrinthVersion
from src.model.pterodactyl import PterodactylFile, PterodactylServer
from src.model.utils import MinecraftLoaders
from src.services import pterodactyl as module
from src.services.modpack import ModpackService
from src.services.pterodactyl import PterodactylService
from tests._actors import EDITOR, OPERATOR, RecordingAudit
from tests._modrinth import RecordedModrinthAPI, load
from tests.conftest import MongoRunner
from tests.test_builds import FakeLoaders

pytestmark = pytest.mark.network

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
FABRIC_API_VERSIONS = [v["id"] for v in load("versions-fabric-api.json")]


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


class FakePanel:
    """Un directorio de un servidor: nombre → (bytes, fecha). Registra cada llamada."""

    def __init__(self) -> None:
        self.files: dict[str, tuple[bytes, datetime]] = {}
        self.calls: list[str] = []
        self.activity: list[tuple[str, datetime]] = []
        self.activity_error: Exception | None = None
        self.node = "n1"

    async def server_get(self, server_id: str) -> PterodactylServer:
        """The server as the panel describes it now: the scan reads its node, not recorded in `calls`."""
        return PterodactylServer(identifier="1a2b3c4d", internal_id=1, uuid=server_id, name="Survival", node=self.node,
                                 description="", status=None, is_suspended=False, is_installing=False,
                                 is_transferring=False)

    async def server_files(self, server_id: str, directory: str) -> list[PterodactylFile]:
        self.calls.append(f"list {directory}")
        entries = [PterodactylFile(name=name, size=len(data), mode_bits="644", is_file=True, is_symlink=False,
                                   mimetype="application/jar", created_at=T0, modified_at=modified)
                   for name, (data, modified) in self.files.items()]
        entries.append(PterodactylFile(name="config", size=0, mode_bits="755", is_file=False, is_symlink=False,
                                       mimetype="inode/directory", created_at=T0, modified_at=T0))
        return entries

    async def server_files_download(self, server_id: str, filepath: str) -> bytes:
        self.calls.append(f"download {filepath}")
        return self.files[filepath.rsplit("/", 1)[-1]][0]

    async def server_activity(self, server_id: str, events: list[str]) -> list[tuple[str, datetime]]:
        self.calls.append("activity")
        if self.activity_error is not None:
            raise self.activity_error
        return list(self.activity)


class HashModrinthAPI(RecordedModrinthAPI):
    """Modrinth por hash: cada sha1 conocido devuelve una versión real grabada, con ese sha1 en su
    archivo primario (no se pueden fabricar bytes con el sha1 de un jar real)."""

    def __init__(self, known: dict[str, str]) -> None:
        super().__init__()
        self.known = known  # sha1 → version id grabado

    async def files_hash_post_many(self, hashes: list[str], algorithm: str = "sha512") -> dict[str, ModrinthVersion]:
        assert algorithm == "sha1"
        self.calls.append(("files_hash_post_many", tuple(hashes)))
        found = {}
        for digest in hashes:
            if digest in self.known:
                version = copy.deepcopy(next(v for v in self.versions if v["id"] == self.known[digest]))
                version["files"][0]["hashes"]["sha1"] = digest
                version["files"][0]["primary"] = True
                found[digest] = ModrinthVersion.parse(**version)
        return found


async def setup(panel: FakePanel, modrinth: HashModrinthAPI, audit: Any = None) -> tuple[PterodactylService, str]:
    server = await PterodactylDatabase().server_insert(PterodactylServer(
        identifier="1a2b3c4d", internal_id=1, uuid="1a2b3c4d-0000", name="Survival", node="n1",
        description="", status=None, is_suspended=False, is_installing=False, is_transferring=False))
    service = PterodactylService(pterodactylAPI=cast(Any, panel), PterodactylDB=PterodactylDatabase(),
                                 modrinthAPI=cast(Any, modrinth), modrinthDB=ModrinthDatabase(),
                                 modpackDB=ModpackDatabase(), audit=audit or RecordingAudit())
    return service, str(server.id)


def test_a_scan_identifies_known_jars_and_reports_the_rest(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[dict[str, tuple[str, str | None]], list[str], set[str]]:
        panel = FakePanel()
        panel.files = {"fabric-api.jar": (b"fabric api", T0), "handmade.jar": (b"by hand", T0),
                       "notes.txt": (b"not a jar", T0)}
        service, server_id = await setup(panel, HashModrinthAPI({sha1(b"fabric api"): FABRIC_API_VERSIONS[0]}))
        scan = await service.scan(server_id, actor=EDITOR)
        stored = {v.version_id async for v in ModrinthVersion.find()}
        return {f.path: (f.status, f.version_id) for f in scan.files}, panel.calls, stored

    files, calls, stored = mongo(scenario)
    assert files == {"/mods/fabric-api.jar": ("identified", FABRIC_API_VERSIONS[0]),
                     "/mods/handmade.jar": ("unknown", None)}
    # Solo lecturas: listar y bajar los dos jars; el .txt y el directorio, ni se bajan.
    assert calls == ["list /mods", "download /mods/fabric-api.jar", "download /mods/handmade.jar"]
    assert FABRIC_API_VERSIONS[0] in stored


def test_a_second_scan_downloads_only_what_changed(mongo: MongoRunner) -> None:
    async def scenario() -> list[list[str]]:
        panel = FakePanel()
        panel.files = {"a.jar": (b"a", T0), "b.jar": (b"b", T0)}
        service, server_id = await setup(panel, HashModrinthAPI({sha1(b"a"): FABRIC_API_VERSIONS[0],
                                                                 sha1(b"b"): FABRIC_API_VERSIONS[1]}))
        downloads = []
        for change, full in [(None, False), (None, False), ("b.jar", False), (None, True)]:
            if change:
                panel.files[change] = (panel.files[change][0], T0 + timedelta(days=1))
            panel.calls.clear()
            await service.scan(server_id, actor=EDITOR, full=full)
            downloads.append([c for c in panel.calls if c.startswith("download")])
        return downloads

    first, unchanged, touched, full = mongo(scenario)
    assert len(first) == 2 and unchanged == [] and touched == ["download /mods/b.jar"] and len(full) == 2


def test_a_jar_past_the_limit_is_reported_and_not_downloaded(mongo: MongoRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "SCAN_MAX_BYTES", 4)

    async def scenario() -> tuple[list[str], list[str]]:
        panel = FakePanel()
        panel.files = {"huge.jar": (b"too many bytes", T0)}
        service, server_id = await setup(panel, HashModrinthAPI({}))
        scan = await service.scan(server_id, actor=EDITOR)
        return [f.status for f in scan.files], [c for c in panel.calls if c.startswith("download")]

    assert mongo(scenario) == (["too_large"], [])


def test_the_latest_scan_is_kept(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[int, int]:
        panel = FakePanel()
        panel.files = {"a.jar": (b"a", T0)}
        service, server_id = await setup(panel, HashModrinthAPI({}))
        await service.scan(server_id, actor=EDITOR)
        panel.files["b.jar"] = (b"b", T0)
        await service.scan(server_id, actor=EDITOR)
        return len((await service.scan_latest(server_id)).files), len(await service.scan_find(server_id))

    assert mongo(scenario) == (2, 2)


# Comparar con un build.


async def build_with_fabric_api(modrinth: HashModrinthAPI) -> tuple[str, str]:
    """Un bundle con Fabric API (va en el servidor) y su build 1, recomendado."""
    service = ModpackService(modpackDB=ModpackDatabase(), modrinthDB=ModrinthDatabase(), modrinthAPI=cast(Any, modrinth),
                             loaderAPI=cast(Any, FakeLoaders()), audit=RecordingAudit())
    bundle = await service.bundle_create(ModpackBundle(name="pack", description="", version="26.3",
                                                       loader=MinecraftLoaders.fabric), actor=EDITOR)
    await service.project_add(str(bundle.id), ["fabric-api"], ProjectOrigin.pack, actor=EDITOR)
    build = await service.build_create(str(bundle.id), actor=EDITOR)
    await service.build_recommend(str(bundle.id), build.number, actor=EDITOR)
    [entry] = build.lock.entries
    return str(bundle.id), entry.version_id


def test_a_server_on_the_recommended_build_is_in_sync(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[bool, list[str]]:
        modrinth = HashModrinthAPI({})
        bundle_id, version_id = await build_with_fabric_api(modrinth)
        modrinth.known[sha1(b"fabric api")] = version_id
        panel = FakePanel()
        panel.files = {"fabric-api.jar": (b"fabric api", T0)}
        service, server_id = await setup(panel, modrinth)
        await service.scan(server_id, actor=EDITOR)
        result = await service.compare(server_id, bundle_id)
        return result.in_sync, [f.path for f in result.matching]

    assert mongo(scenario) == (True, ["/mods/fabric-api.jar"])


def test_a_server_with_an_older_version_and_a_jar_by_hand_is_not(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[list[tuple[str, str]], list[str], list[str]]:
        modrinth = HashModrinthAPI({})
        bundle_id, version_id = await build_with_fabric_api(modrinth)
        older = next(v for v in FABRIC_API_VERSIONS if v != version_id)
        modrinth.known[sha1(b"old fabric api")] = older
        panel = FakePanel()
        panel.files = {"fabric-api.jar": (b"old fabric api", T0), "handmade.jar": (b"by hand", T0)}
        service, server_id = await setup(panel, modrinth)
        await service.scan(server_id, actor=EDITOR)
        result = await service.compare(server_id, bundle_id, build=1)
        return ([(f.version_id or "", e.version_id) for f, e in result.different_version],
                [f.path for f in result.unknown], [e.project_id for e in result.missing])

    different, unknown, missing = mongo(scenario)
    assert len(different) == 1 and different[0][0] != different[0][1]
    assert unknown == ["/mods/handmade.jar"] and missing == []


def test_comparing_needs_a_scan_and_a_build(mongo: MongoRunner) -> None:
    async def scenario() -> list[int]:
        modrinth = HashModrinthAPI({})
        service, server_id = await setup(FakePanel(), modrinth)
        bundle = await ModpackService(modpackDB=ModpackDatabase(), modrinthDB=ModrinthDatabase(),
                                      modrinthAPI=cast(Any, modrinth), loaderAPI=cast(Any, FakeLoaders()),
                                      audit=RecordingAudit()).bundle_create(
            ModpackBundle(name="empty", description="", version="26.3", loader=MinecraftLoaders.fabric), actor=EDITOR)
        codes = []
        for call in (lambda: service.compare(server_id, str(bundle.id)), lambda: service.scan_latest(server_id)):
            with pytest.raises(HTTPException) as refused:
                await call()
            codes.append(refused.value.status_code)
        return codes

    assert mongo(scenario) == [409, 404]


class WrongAnswerModrinthAPI(HashModrinthAPI):
    """Modrinth contesta con una versión cuyos archivos no tienen el sha1 pedido."""

    async def files_hash_post_many(self, hashes: list[str], algorithm: str = "sha512") -> dict[str, ModrinthVersion]:
        version = next(v for v in self.versions if v["id"] == FABRIC_API_VERSIONS[0])
        return {digest: ModrinthVersion.parse(**copy.deepcopy(version)) for digest in hashes}


def test_a_version_whose_files_do_not_have_that_hash_does_not_identify_the_jar(mongo: MongoRunner) -> None:
    async def scenario() -> list[str]:
        panel = FakePanel()
        panel.files = {"a.jar": (b"a", T0)}
        service, server_id = await setup(panel, WrongAnswerModrinthAPI({}))
        return [f.status for f in (await service.scan(server_id, actor=EDITOR)).files]

    assert mongo(scenario) == ["unknown"]


def test_an_unknown_jar_modrinth_learns_later_is_identified_without_downloading_it_again(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[list[str], list[str], list[str]]:
        panel = FakePanel()
        panel.files = {"late.jar": (b"published later", T0)}
        modrinth = HashModrinthAPI({})
        service, server_id = await setup(panel, modrinth)
        first = [str(f.status) for f in (await service.scan(server_id, actor=EDITOR)).files]
        modrinth.known[sha1(b"published later")] = FABRIC_API_VERSIONS[0]
        panel.calls.clear()
        second = [str(f.status) for f in (await service.scan(server_id, actor=EDITOR)).files]
        return first, second, [c for c in panel.calls if c.startswith("download")]

    assert mongo(scenario) == (["unknown"], ["identified"], [])


# El bundle de un servidor.


def test_a_server_linked_to_a_bundle_compares_against_it_without_naming_it(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[str | None, bool]:
        modrinth = HashModrinthAPI({})
        bundle_id, version_id = await build_with_fabric_api(modrinth)
        modrinth.known[sha1(b"fabric api")] = version_id
        panel = FakePanel()
        panel.files = {"fabric-api.jar": (b"fabric api", T0)}
        service, server_id = await setup(panel, modrinth)
        linked = await service.server_link_bundle(server_id, bundle_id, actor=OPERATOR)
        await service.scan(server_id, actor=EDITOR)
        return linked.bundle_id, (await service.compare(server_id)).in_sync

    bundle_id, in_sync = mongo(scenario)
    assert bundle_id is not None and in_sync


def test_scan_of_a_linked_server_stores_its_drift(mongo: MongoRunner) -> None:
    """A scan of a server linked to a bundle with a build stores how it compares (F37), also one
    without an actor (the scheduler's, a dry run's). Only a real update's preflight scan does not
    (`record_drift=False`, tests/test_update.py). The dashboard reads it; an unlinked server has none."""
    async def scenario() -> tuple[str | None, str | None, str | None]:
        modrinth = HashModrinthAPI({})
        bundle_id, version_id = await build_with_fabric_api(modrinth)
        modrinth.known[sha1(b"fabric api")] = version_id
        panel = FakePanel()
        panel.files = {"fabric-api.jar": (b"fabric api", T0), "handmade.jar": (b"by hand", T0)}
        service, server_id = await setup(panel, modrinth)
        unlinked = await service.scan(server_id, actor=None)
        await service.server_link_bundle(server_id, bundle_id, actor=OPERATOR)
        linked = await service.scan(server_id, actor=None)
        return unlinked.drift, linked.drift, (await service.scan_latest(server_id)).drift

    unlinked, returned, stored = mongo(scenario)
    assert unlinked is None
    assert returned is not None and returned.startswith("⚠️") and "handmade.jar" in returned and stored == returned


def test_comparison_names_versions_by_number_or_by_jar(mongo: MongoRunner) -> None:
    """Version ids read as numbers: the server's from the catalog (the scan stored them), the
    build's from its lock; a version neither knows reads as its jar's name."""
    async def scenario() -> tuple[dict[str, str], dict[str, str], str]:
        modrinth = HashModrinthAPI({})
        bundle_id, version_id = await build_with_fabric_api(modrinth)
        older = next(v for v in FABRIC_API_VERSIONS if v != version_id)
        modrinth.known[sha1(b"old fabric api")] = older
        panel = FakePanel()
        panel.files = {"fabric-api.jar": (b"old fabric api", T0)}
        service, server_id = await setup(panel, modrinth)
        await service.scan(server_id, actor=EDITOR)
        comparison = await service.compare(server_id, bundle_id)
        labels = await service.compare_names(comparison)
        numbers = {v["id"]: v["version_number"] for v in modrinth.versions}
        [(file, _)] = comparison.different_version
        unknown_version = file.model_copy(update={"version_id": "notInCatalog", "path": "/mods/mystery-1.0.jar"})
        fallback = await service.compare_names(comparison.model_copy(update={"matching": [unknown_version]}))
        return labels, {older: numbers[older], version_id: numbers[version_id]}, fallback["notInCatalog"]

    labels, expected, fallback = mongo(scenario)
    assert labels == expected and fallback == "mystery-1.0.jar"


def test_linking_needs_an_existing_bundle_and_comparing_needs_a_link(mongo: MongoRunner) -> None:
    async def scenario() -> list[int]:
        service, server_id = await setup(FakePanel(), HashModrinthAPI({}))
        codes = []
        for call in (lambda: service.server_link_bundle(server_id, "000000000000000000000000", actor=OPERATOR),
                     lambda: service.compare(server_id)):
            with pytest.raises(HTTPException) as refused:
                await call()
            codes.append(refused.value.status_code)
        return codes

    assert mongo(scenario) == [404, 409]


def test_refreshing_a_server_from_the_panel_keeps_its_bundle(mongo: MongoRunner) -> None:
    class Panel(FakePanel):
        async def server_get(self, uuid: str) -> PterodactylServer:
            return PterodactylServer(identifier="1a2b3c4d", internal_id=1, uuid=uuid, name="Renamed", node="n1",
                                     description="", status=None, is_suspended=False, is_installing=False,
                                     is_transferring=False)

    async def scenario() -> tuple[str, str | None]:
        modrinth = HashModrinthAPI({})
        bundle_id, _ = await build_with_fabric_api(modrinth)
        service, server_id = await setup(Panel(), modrinth)
        await service.server_link_bundle(server_id, bundle_id, actor=OPERATOR)
        refreshed = await service.server_update(server_id, actor=EDITOR)
        return refreshed.name, refreshed.bundle_id

    name, bundle_id = mongo(scenario)
    assert name == "Renamed" and bundle_id is not None


# T1 (docs/superpowers/plans/2026-10-09-pendientes.md): S4, S5, U3 y el avance.


def _fabric_jar(mod_id: str, name: str) -> bytes:
    import io
    import json
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("fabric.mod.json", json.dumps({"schemaVersion": 1, "id": mod_id, "name": name, "version": "1"}))
    return buffer.getvalue()


def test_a_scan_keeps_creation_times_and_the_metadata_of_unknown_jars_only(mongo: MongoRunner) -> None:
    async def scenario() -> dict[str, tuple[Any, ...]]:
        panel = FakePanel()
        panel.files = {"fabric-api.jar": (b"fabric api", T0), "create.jar": (_fabric_jar("create", "Create"), T0)}
        service, server_id = await setup(panel, HashModrinthAPI({sha1(b"fabric api"): FABRIC_API_VERSIONS[0]}))
        scan = await service.scan(server_id, actor=EDITOR)
        return {f.path: (f.created_at, f.metadata_read, f.metadata.mod_id if f.metadata else None) for f in scan.files}

    files = mongo(scenario)
    assert files["/mods/create.jar"] == (T0, True, "create")
    assert files["/mods/fabric-api.jar"] == (T0, False, None)  # identified: no metadata kept


def test_a_seeded_jar_is_downloaded_once_by_the_next_scan(mongo: MongoRunner) -> None:
    # U3's risk (owner, 2026-10-12): seeded hashes are confirmed by the next scan.
    async def scenario() -> tuple[list[str], list[str], list[str]]:
        panel = FakePanel()
        panel.files = {"a.jar": (b"a", T0)}
        service, server_id = await setup(panel, HashModrinthAPI({sha1(b"a"): FABRIC_API_VERSIONS[0]}))
        seeds = {"/mods/a.jar": (1, sha1(b"a"), hashlib.sha512(b"a").hexdigest())}
        rounds = []
        for use_seeds in (True, False, False):
            panel.calls.clear()
            await service.scan(server_id, actor=None, seeds=seeds if use_seeds else None)
            rounds.append([c for c in panel.calls if c.startswith("download")])
        return rounds[0], rounds[1], rounds[2]

    seeded, confirming, after = mongo(scenario)
    assert seeded == [] and confirming == ["download /mods/a.jar"] and after == []


def test_a_scan_says_how_far_it_is(mongo: MongoRunner) -> None:
    async def scenario() -> list[tuple[str, int, int]]:
        panel = FakePanel()
        panel.files = {"a.jar": (b"a", T0), "b.jar": (b"b", T0)}
        service, server_id = await setup(panel, HashModrinthAPI({}))
        seen: list[tuple[str, int, int]] = []

        async def report(label: str, done: int, total: int) -> None:
            seen.append((label, done, total))

        await service.scan(server_id, actor=None, report=report)
        return seen

    seen = mongo(scenario)
    assert seen[0] == ("Leyendo los jars del servidor", 0, 2) and seen[-1] == ("Leyendo los jars del servidor", 2, 2)



# A backup restore keeps old mtimes (code review, 2026-10-12; research
# docs/research/2026-10-12-actividad-pterodactyl.md): the server's activity says so instead.


def test_a_restore_since_the_last_scan_makes_the_next_scan_full(mongo: MongoRunner) -> None:
    from datetime import timedelta

    async def scenario() -> tuple[list[str], list[str], bool, bool]:
        panel = FakePanel()
        panel.files = {"fabric-api.jar": (b"fabric api", T0)}
        service, server_id = await setup(panel, HashModrinthAPI({}))
        first = await service.scan(server_id, actor=None)
        panel.calls.clear()
        quiet = await service.scan(server_id, actor=None)
        quiet_calls = list(panel.calls)
        panel.calls.clear()
        panel.activity = [("server:backup.restore-complete", quiet.scanned_at + timedelta(seconds=5))]
        restored = await service.scan(server_id, actor=None)
        return quiet_calls, list(panel.calls), quiet.full, restored.full

    quiet_calls, restored_calls, quiet_full, restored_full = mongo(scenario)
    assert quiet_calls == ["activity", "list /mods"] and quiet_full is False
    assert restored_calls == ["activity", "list /mods", "download /mods/fabric-api.jar"] and restored_full is True


def test_when_the_activity_cannot_be_read_the_scan_is_full(mongo: MongoRunner) -> None:
    # Failing closed: a restore we cannot rule out costs one full scan, never a stale cache.
    from fastapi import HTTPException

    async def scenario() -> bool:
        panel = FakePanel()
        panel.files = {"fabric-api.jar": (b"fabric api", T0)}
        service, server_id = await setup(panel, HashModrinthAPI({}))
        await service.scan(server_id, actor=None)
        panel.activity_error = HTTPException(status_code=403, detail="forbidden")
        return (await service.scan(server_id, actor=None)).full

    assert mongo(scenario) is True


def test_a_comparison_that_breaks_leaves_the_scan_stored_without_drift(mongo: MongoRunner) -> None:
    # Roadmap (round 1 review): _store_drift caught only HTTPException; comparing is a nicety there.
    async def scenario() -> tuple[Any, Any]:
        panel = FakePanel()
        panel.files = {"fabric-api.jar": (b"fabric api", T0)}
        service, server_id = await setup(panel, HashModrinthAPI({}))

        async def broken(server_id: str) -> Any:
            raise KeyError("lock")

        service.drift = broken  # type: ignore[method-assign]
        scan = await service.scan(server_id, actor=None)
        return scan.drift, await service.scan_latest(server_id)

    drift, latest = mongo(scenario)
    assert drift is None and latest is not None


@pytest.mark.parametrize("status", [409, 429])
def test_a_busy_or_limited_panel_raises_instead_of_scanning_everything(mongo: MongoRunner, status: int) -> None:
    # 409: restoring, installing or moving (scan later). 429 (code review, 2026-10-12): a full scan
    # would make the limit worse; the job retries with its backoff instead.
    from fastapi import HTTPException

    async def scenario() -> tuple[int, list[str]]:
        panel = FakePanel()
        panel.files = {"fabric-api.jar": (b"fabric api", T0)}
        service, server_id = await setup(panel, HashModrinthAPI({}))
        await service.scan(server_id, actor=None)
        panel.calls.clear()
        panel.activity_error = HTTPException(status_code=status, detail="busy")
        with pytest.raises(HTTPException) as raised:
            await service.scan(server_id, actor=None)
        return raised.value.status_code, list(panel.calls)

    code, calls = mongo(scenario)
    assert code == status and calls == ["activity"]  # nothing listed, nothing downloaded


def test_an_event_before_the_last_scan_does_not_make_it_full_and_a_very_old_scan_does(mongo: MongoRunner) -> None:
    from datetime import timedelta

    async def scenario() -> tuple[bool, list[str], bool]:
        panel = FakePanel()
        panel.files = {"fabric-api.jar": (b"fabric api", T0)}
        service, server_id = await setup(panel, HashModrinthAPI({}))
        first = await service.scan(server_id, actor=None)
        panel.activity = [("server:backup.restore-complete", first.scanned_at - timedelta(minutes=10))]
        quiet = await service.scan(server_id, actor=None)
        # A scan older than the activity keeps (90 days) is full without asking for the activity.
        from src.model.pterodactyl import ServerScan
        await ServerScan.get_pymongo_collection().update_many(
            {}, {"$set": {"scanned_at": quiet.scanned_at - timedelta(days=200)}})
        panel.calls.clear()
        old = await service.scan(server_id, actor=None)
        return quiet.full, list(panel.calls), old.full

    quiet_full, calls, old_full = mongo(scenario)
    assert quiet_full is False
    assert old_full is True and "activity" not in calls


def test_the_baseline_is_when_the_scan_started(mongo: MongoRunner) -> None:
    # Code review (2026-10-12): a restore between listing and the end of a long scan would be missed
    # if the scan were dated by its end.
    from datetime import datetime, timezone

    async def scenario() -> tuple[Any, Any]:
        panel = FakePanel()
        panel.files = {"fabric-api.jar": (b"fabric api", T0)}
        service, server_id = await setup(panel, HashModrinthAPI({}))
        started = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
        service.clock = lambda: started  # type: ignore[assignment]
        scan = await service.scan(server_id, actor=None)
        return scan.scanned_at, started

    scanned_at, started = mongo(scenario)
    assert scanned_at.replace(tzinfo=None) == started.replace(tzinfo=None)


def test_a_full_scan_stores_why_and_the_node_and_a_moved_server_scans_again(mongo: MongoRunner) -> None:
    """Owner, 2026-10-21: the reason and the node are stored and read back; a transfer to another node
    leaves no activity event, so the node change makes the scan full."""
    async def scenario() -> list[tuple[Any, ...]]:
        panel = FakePanel()
        panel.files = {"fabric-api.jar": (b"fabric api", T0)}
        service, server_id = await setup(panel, HashModrinthAPI({}))
        seen = []
        for node in ("n1", "n1", "n2"):
            panel.node = node
            panel.calls.clear()
            await service.scan(server_id, actor=None)
            latest = await service.scan_latest(server_id)
            seen.append((latest.full, latest.full_reason, latest.node, "download /mods/fabric-api.jar" in panel.calls))
        return seen

    assert mongo(scenario) == [(True, "first", "n1", True), (False, None, "n1", False), (True, "node_changed", "n2", True)]


def test_scanning_a_linked_server_offers_its_version_of_a_pinned_mod_as_the_server_origin(mongo: MongoRunner) -> None:
    """Owner, 2026-10-21 (d-7985ee-fce4ff): the pinned mod the server runs at another version gets a
    «servidor» origin with the server's name and its validation; the pin stays in use."""
    from src.model.modpack import PinSource

    async def scenario() -> tuple[Any, ...]:
        modrinth = HashModrinthAPI({})
        bundle_id, version_id = await build_with_fabric_api(modrinth)
        project_id = (await ModpackDatabase().bundle_get(bundle_id)).projects[0].project_id
        await ModpackDatabase().bundle_pin_set(bundle_id, project_id, version_id)
        older = next(v for v in FABRIC_API_VERSIONS if v != version_id)
        modrinth.known[sha1(b"old fabric api")] = older
        panel = FakePanel()
        panel.files = {"fabric-api.jar": (b"old fabric api", T0)}
        service, server_id = await setup(panel, modrinth)
        await service.server_link_bundle(server_id, bundle_id, actor=OPERATOR)
        await service.scan(server_id, actor=None)
        bundle = await ModpackDatabase().bundle_get(bundle_id)
        record = bundle.pin_origins[project_id]
        server = record.origin(PinSource.server)
        assert server is not None
        return bundle.pins[project_id], record.selected, server.version_id, server.label, server.valid, older, version_id

    pinned, selected, offered, label, valid, older, version_id = mongo(scenario)
    assert pinned == version_id and selected == PinSource.admin
    assert (offered, label) == (older, "Survival") and valid is not None
