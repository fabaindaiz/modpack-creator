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
from tests._modrinth import RecordedModrinthAPI, load
from tests.conftest import MongoRunner

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


async def setup(panel: FakePanel, modrinth: HashModrinthAPI) -> tuple[PterodactylService, str]:
    server = await PterodactylDatabase().server_insert(PterodactylServer(
        identifier="1a2b3c4d", internal_id=1, uuid="1a2b3c4d-0000", name="Survival", node="n1",
        description="", status=None, is_suspended=False, is_installing=False, is_transferring=False))
    service = PterodactylService(pterodactylAPI=cast(Any, panel), PterodactylDB=PterodactylDatabase(),
                                 modrinthAPI=cast(Any, modrinth), modrinthDB=ModrinthDatabase(),
                                 modpackDB=ModpackDatabase())
    return service, str(server.id)


def test_a_scan_identifies_known_jars_and_reports_the_rest(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[dict[str, tuple[str, str | None]], list[str], set[str]]:
        panel = FakePanel()
        panel.files = {"fabric-api.jar": (b"fabric api", T0), "handmade.jar": (b"by hand", T0),
                       "notes.txt": (b"not a jar", T0)}
        service, server_id = await setup(panel, HashModrinthAPI({sha1(b"fabric api"): FABRIC_API_VERSIONS[0]}))
        scan = await service.scan(server_id)
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
            await service.scan(server_id, full=full)
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
        scan = await service.scan(server_id)
        return [f.status for f in scan.files], [c for c in panel.calls if c.startswith("download")]

    assert mongo(scenario) == (["too_large"], [])


def test_the_latest_scan_is_kept(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[int, int]:
        panel = FakePanel()
        panel.files = {"a.jar": (b"a", T0)}
        service, server_id = await setup(panel, HashModrinthAPI({}))
        await service.scan(server_id)
        panel.files["b.jar"] = (b"b", T0)
        await service.scan(server_id)
        return len((await service.scan_latest(server_id)).files), len(await service.scan_find(server_id))

    assert mongo(scenario) == (2, 2)


# Comparar con un build.


async def build_with_fabric_api(modrinth: HashModrinthAPI) -> tuple[str, str]:
    """Un bundle con Fabric API (va en el servidor) y su build 1, recomendado."""
    service = ModpackService(modpackDB=ModpackDatabase(), modrinthDB=ModrinthDatabase(), modrinthAPI=cast(Any, modrinth))
    bundle = await service.bundle_create(ModpackBundle(name="pack", description="", version="26.3",
                                                       loader=MinecraftLoaders.fabric))
    await service.project_add(str(bundle.id), ["fabric-api"], ProjectOrigin.pack)
    build = await service.build_create(str(bundle.id))
    await service.build_recommend(str(bundle.id), build.number)
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
        await service.scan(server_id)
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
        await service.scan(server_id)
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
                                      modrinthAPI=cast(Any, modrinth)).bundle_create(
            ModpackBundle(name="empty", description="", version="26.3", loader=MinecraftLoaders.fabric))
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
        return [f.status for f in (await service.scan(server_id)).files]

    assert mongo(scenario) == ["unknown"]


def test_an_unknown_jar_modrinth_learns_later_is_identified_without_downloading_it_again(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[list[str], list[str], list[str]]:
        panel = FakePanel()
        panel.files = {"late.jar": (b"published later", T0)}
        modrinth = HashModrinthAPI({})
        service, server_id = await setup(panel, modrinth)
        first = [str(f.status) for f in (await service.scan(server_id)).files]
        modrinth.known[sha1(b"published later")] = FABRIC_API_VERSIONS[0]
        panel.calls.clear()
        second = [str(f.status) for f in (await service.scan(server_id)).files]
        return first, second, [c for c in panel.calls if c.startswith("download")]

    assert mongo(scenario) == (["unknown"], ["identified"], [])
