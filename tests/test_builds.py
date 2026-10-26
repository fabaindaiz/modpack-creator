"""El contenido de un modpack y sus builds (i-7985ee-063a3a, i-7985ee-098611).

- Contenido: cada proyecto lleva su origen. Lo que agrega el admin pasa por la resolución igual
  que el pack (d-7985ee-b15dd4) y sobrevive a reemplazar el contenido del pack.
- Builds: cada uno congela un lock con su número; nunca se edita. `latest` sigue al último,
  `recommended` se mueve a mano, y volver atrás es moverlo.
Contra MongoDB real y respuestas grabadas de Modrinth.
"""
import asyncio
import io
import json
import zipfile
from datetime import datetime, timezone
from typing import Any, cast

import pytest
from fastapi import HTTPException

from src.database.modpack import ModpackDatabase
from src.database.modrinth import ModrinthDatabase
from src.exceptions import BuildBlocked
from src.library.mrpack import validate_index
from src.model.modpack import ModpackBundle, ModpackBundlePatch, ModpackResolution, ProjectOrigin
from src.model.utils import MinecraftLoaders
from src.library.resolution import request_digest
from src.services.modpack import ModpackService
from tests._actors import EDITOR, RecordingAudit
from tests._modrinth import FABRIC_API, IRIS, SODIUM, RecordedModrinthAPI
from tests.conftest import MongoRunner

pytestmark = pytest.mark.network


class FakeLoaders:
    """La versión estable de cada loader que tiene meta; ninguna para los demás."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, str]] = []

    async def loader_version(self, loader: str, game_version: str) -> str | None:
        self.asked.append((loader, game_version))
        return {"fabric": "0.19.5", "quilt": "0.24.0"}.get(loader)


async def empty_bundle(**fields: Any) -> tuple[ModpackService, str]:
    service = ModpackService(modpackDB=ModpackDatabase(), modrinthDB=ModrinthDatabase(),
                             modrinthAPI=cast(Any, RecordedModrinthAPI()), loaderAPI=cast(Any, FakeLoaders()), audit=RecordingAudit())
    created = await service.bundle_create(ModpackBundle(
        name="pack", description="", version="26.3", loader=MinecraftLoaders.fabric, **fields), actor=EDITOR)
    return service, str(created.id)


def content(projects: list[Any]) -> dict[str, str]:
    return {p.project_id: p.origin.value for p in projects}


# Contenido.


def test_projects_are_added_by_slug_and_kept_by_modrinth_id(mongo: MongoRunner) -> None:
    async def scenario() -> dict[str, str]:
        service, bundle_id = await empty_bundle()
        return content(await service.project_add(bundle_id, ["iris"], ProjectOrigin.pack, actor=EDITOR))

    assert mongo(scenario) == {IRIS: "pack"}


def test_a_project_modrinth_does_not_know_is_refused(mongo: MongoRunner) -> None:
    async def scenario() -> int:
        service, bundle_id = await empty_bundle()
        with pytest.raises(HTTPException) as refused:
            await service.project_add(bundle_id, ["iris", "no-such-mod"], ProjectOrigin.pack, actor=EDITOR)
        assert "no-such-mod" in str(refused.value.detail)
        return len(await service.project_find(bundle_id))

    assert mongo(scenario) == 0


def test_what_the_admin_added_survives_replacing_the_pack(mongo: MongoRunner) -> None:
    async def scenario() -> dict[str, str]:
        service, bundle_id = await empty_bundle()
        await service.project_add(bundle_id, ["iris"], ProjectOrigin.pack, actor=EDITOR)
        await service.project_add(bundle_id, ["fabric-api"], ProjectOrigin.admin, actor=EDITOR)
        # El pack nuevo trae Sodium y también Fabric API, que el admin ya tenía: sigue siendo suyo.
        return content(await service.pack_replace(bundle_id, ["sodium", "fabric-api"], actor=EDITOR))

    assert mongo(scenario) == {FABRIC_API: "admin", SODIUM: "pack"}


def test_concurrent_additions_are_all_kept(mongo: MongoRunner) -> None:
    async def scenario() -> dict[str, str]:
        service, bundle_id = await empty_bundle()
        await asyncio.gather(service.project_add(bundle_id, ["iris"], ProjectOrigin.pack, actor=EDITOR),
                             service.project_add(bundle_id, ["fabric-api"], ProjectOrigin.admin, actor=EDITOR))
        return content(await service.project_find(bundle_id))

    assert mongo(scenario) == {IRIS: "pack", FABRIC_API: "admin"}


def test_removing_a_project(mongo: MongoRunner) -> None:
    async def scenario() -> dict[str, str]:
        service, bundle_id = await empty_bundle()
        await service.project_add(bundle_id, ["iris", "fabric-api"], ProjectOrigin.pack, actor=EDITOR)
        return content((await service.project_remove(bundle_id, IRIS, actor=EDITOR)).projects)

    assert mongo(scenario) == {FABRIC_API: "pack"}


def test_the_lock_says_where_each_project_came_from(mongo: MongoRunner) -> None:
    async def scenario() -> dict[str, tuple[str | None, str]]:
        service, bundle_id = await empty_bundle()
        await service.project_add(bundle_id, ["iris"], ProjectOrigin.pack, actor=EDITOR)
        await service.project_add(bundle_id, ["fabric-api"], ProjectOrigin.admin, actor=EDITOR)
        lock = await service.resolve_fetching(bundle_id)
        return {e.project_id: (e.origin, e.reason) for e in lock.entries}

    entries = mongo(scenario)
    assert entries[IRIS] == ("pack", "en el pack")
    assert entries[FABRIC_API] == ("admin", "agregado por el admin")
    assert entries[SODIUM][0] is None and "requerida" in entries[SODIUM][1]


# Builds.


def test_builds_are_numbered_and_latest_follows_the_newest(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[list[int], int | None, int | None]:
        service, bundle_id = await empty_bundle()
        await service.project_add(bundle_id, ["iris"], ProjectOrigin.pack, actor=EDITOR)
        first = await service.build_create(bundle_id, actor=EDITOR)
        second = await service.build_create(bundle_id, actor=EDITOR)
        stored = await service.bundle_get(bundle_id)
        return [first.number, second.number], stored.latest_build, stored.recommended_build

    assert mongo(scenario) == ([1, 2], 2, None)


def test_a_build_does_not_change_when_the_bundle_does(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[set[str], set[str]]:
        service, bundle_id = await empty_bundle()
        await service.project_add(bundle_id, ["iris"], ProjectOrigin.pack, actor=EDITOR)
        await service.build_create(bundle_id, actor=EDITOR)
        await service.project_add(bundle_id, ["fabric-api"], ProjectOrigin.admin, actor=EDITOR)
        await service.build_create(bundle_id, actor=EDITOR)
        first, second = await service.build_get(bundle_id, 1), await service.build_get(bundle_id, 2)
        return {e.project_id for e in first.lock.entries}, {e.project_id for e in second.lock.entries}

    assert mongo(scenario) == ({IRIS, SODIUM}, {IRIS, SODIUM, FABRIC_API})


def test_recommending_a_build_moves_the_pointer_and_going_back_is_moving_it_again(mongo: MongoRunner) -> None:
    async def scenario() -> list[int | None]:
        service, bundle_id = await empty_bundle()
        await service.project_add(bundle_id, ["iris"], ProjectOrigin.pack, actor=EDITOR)
        await service.build_create(bundle_id, actor=EDITOR)
        await service.build_create(bundle_id, actor=EDITOR)
        pointers = [(await service.build_recommend(bundle_id, 2, actor=EDITOR)).recommended_build]
        pointers.append((await service.build_recommend(bundle_id, 1, actor=EDITOR)).recommended_build)
        with pytest.raises(HTTPException) as missing:
            await service.build_recommend(bundle_id, 7, actor=EDITOR)
        assert missing.value.status_code == 404
        return pointers

    assert mongo(scenario) == [2, 1]


def test_a_lock_with_problems_is_not_built(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[int, list[Any]]:
        service, bundle_id = await empty_bundle(pins={IRIS: "no-such-version"})
        await service.project_add(bundle_id, ["iris"], ProjectOrigin.pack, actor=EDITOR)
        with pytest.raises(HTTPException) as refused:
            await service.build_create(bundle_id, actor=EDITOR)
        return refused.value.status_code, await service.build_find(bundle_id)

    assert mongo(scenario) == (409, [])


def test_build_create_resolves_again_and_never_reads_the_saved_lock(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, Any, Any]:
        service, bundle_id = await empty_bundle()
        await service.project_add(bundle_id, ["iris"], ProjectOrigin.pack, actor=EDITOR)
        fresh = await service.resolve_fetching(bundle_id)
        bundle = await service.modpackDB.bundle_get(bundle_id)
        # A current resolution (same digest) whose lock holds nothing: a build must not use it.
        await service.modpackDB.resolution_save(ModpackResolution(
            bundle_id=bundle.id, resolved_at=datetime.now(timezone.utc),  # type: ignore[arg-type]
            request_digest=request_digest(await service._request(bundle)), lock=fresh.model_copy(update={"entries": []})))
        build = await service.build_create(bundle_id, actor=EDITOR)
        return fresh, build, (await service.resolution(bundle_id))[0]  # type: ignore[index]

    fresh, build, saved = mongo(scenario)
    assert build.lock == fresh and build.lock.entries
    assert saved.lock == build.lock


def test_undecided_build_raises_build_blocked_still_a_409(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[int, bool, list[str], str, Any]:
        api = RecordedModrinthAPI()
        for version in api.versions:
            if version["project_id"] == IRIS:
                version["environment"] = "client_or_server"
        service = ModpackService(modpackDB=ModpackDatabase(), modrinthDB=ModrinthDatabase(), modrinthAPI=cast(Any, api),
                                 loaderAPI=cast(Any, FakeLoaders()), audit=RecordingAudit())
        created = await service.bundle_create(ModpackBundle(
            name="pack", description="", version="26.3", loader=MinecraftLoaders.fabric), actor=EDITOR)
        bundle_id = str(created.id)
        await service.project_add(bundle_id, ["iris"], ProjectOrigin.pack, actor=EDITOR)
        with pytest.raises(BuildBlocked) as caught:
            await service.build_create(bundle_id, actor=EDITOR)
        error = caught.value
        saved = (await service.resolution(bundle_id))[0]  # type: ignore[index]
        assert saved.lock == error.lock and await service.build_find(bundle_id) == []
        return (error.status_code, error.only_undecided, [e.project_id for e in error.lock.undecided],
                error.detail, saved.lock)

    status, only_undecided, undecided, detail, _ = mongo(scenario)
    assert status == 409 and only_undecided and undecided == [IRIS]
    assert detail == f"No se puede congelar este lock: {IRIS}: falta decidir si va en el servidor"


def test_a_lock_with_problems_is_blocked_but_not_only_undecided(mongo: MongoRunner) -> None:
    async def scenario() -> bool:
        service, bundle_id = await empty_bundle(pins={IRIS: "no-such-version"})
        await service.project_add(bundle_id, ["iris"], ProjectOrigin.pack, actor=EDITOR)
        with pytest.raises(BuildBlocked) as caught:
            await service.build_create(bundle_id, actor=EDITOR)
        return caught.value.only_undecided

    assert mongo(scenario) is False


def test_two_builds_created_at_once_get_different_numbers(mongo: MongoRunner) -> None:
    async def scenario() -> list[int]:
        service, bundle_id = await empty_bundle()
        await service.project_add(bundle_id, ["iris"], ProjectOrigin.pack, actor=EDITOR)
        results = await asyncio.gather(service.build_create(bundle_id, actor=EDITOR), service.build_create(bundle_id, actor=EDITOR),
                                       return_exceptions=True)
        return sorted(r.number for r in results if not isinstance(r, BaseException))

    numbers = mongo(scenario)
    # Uno puede perder la carrera y pedir reintentar (409), pero nunca dos con el mismo número.
    assert len(numbers) == len(set(numbers)) and numbers[0] == 1


# Exportar (i-7985ee-7352b3).


def test_a_build_freezes_the_loader_version(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[str | None, str | None]:
        service, bundle_id = await empty_bundle()
        await service.project_add(bundle_id, ["iris"], ProjectOrigin.pack, actor=EDITOR)
        first = await service.build_create(bundle_id, actor=EDITOR)
        await service.bundle_update(bundle_id, ModpackBundlePatch(loader_version="0.18.0"), actor=EDITOR)
        pinned = await service.build_create(bundle_id, actor=EDITOR)
        return first.loader_version, pinned.loader_version

    assert mongo(scenario) == ("0.19.5", "0.18.0")


def test_a_build_exports_as_a_valid_mrpack(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[str, dict[str, Any]]:
        service, bundle_id = await empty_bundle()
        await service.project_add(bundle_id, ["iris", "fabric-api"], ProjectOrigin.pack, actor=EDITOR)
        await service.build_create(bundle_id, actor=EDITOR)
        filename, data = await service.build_mrpack(bundle_id, 1)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return filename, json.loads(archive.read("modrinth.index.json"))

    filename, index = mongo(scenario)
    assert filename == "pack-1.mrpack"
    assert validate_index(index) == []
    assert index["dependencies"] == {"minecraft": "26.3", "fabric-loader": "0.19.5"}
    assert sorted(f["path"].split("/")[0] for f in index["files"]) == ["mods", "mods", "mods"]
    sides = {f["path"].split("/")[1].split("-")[0]: f["env"] for f in index["files"]}
    assert sides["iris"] == {"client": "required", "server": "unsupported"}
    assert all(d.startswith("https://cdn.modrinth.com/") for f in index["files"] for d in f["downloads"])


def test_a_build_without_a_loader_version_does_not_export(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[str | None, int]:
        service, bundle_id = await empty_bundle()
        await service.bundle_update(bundle_id, ModpackBundlePatch(loader=MinecraftLoaders.neoforge), actor=EDITOR)
        build = await service.build_create(bundle_id, actor=EDITOR)
        with pytest.raises(HTTPException) as refused:
            await service.build_mrpack(bundle_id, build.number)
        return build.loader_version, refused.value.status_code

    assert mongo(scenario) == (None, 409)


def test_a_new_build_is_announced(mongo: MongoRunner) -> None:
    from tests.test_notify import Recorder

    async def scenario() -> list[str]:
        recorder = Recorder()
        service = ModpackService(modpackDB=ModpackDatabase(), modrinthDB=ModrinthDatabase(),
                                 modrinthAPI=cast(Any, RecordedModrinthAPI()), loaderAPI=cast(Any, FakeLoaders()), audit=RecordingAudit(),
                                 notifier=cast(Any, recorder))
        bundle = await service.bundle_create(ModpackBundle(name="Survival pack", description="", version="26.3",
                                                           loader=MinecraftLoaders.fabric), actor=EDITOR)
        await service.project_add(str(bundle.id), ["iris"], ProjectOrigin.pack, actor=EDITOR)
        await service.build_create(str(bundle.id), actor=EDITOR)
        return recorder.sent

    [message] = mongo(scenario)
    assert "Survival pack" in message and "build 1" in message


def test_a_build_freezes_the_bundles_local_files_and_the_mrpack_leaves_them_out(mongo: MongoRunner) -> None:
    from src.library.scan import LocalFile

    async def scenario() -> tuple[list[str], list[str]]:
        service, bundle_id = await empty_bundle()
        await service.project_add(bundle_id, ["iris"], ProjectOrigin.pack, actor=EDITOR)
        await service.modpackDB.bundle_local_file_add(bundle_id, LocalFile(filename="private.jar", sha512="p" * 128, sha1="p" * 40, size=10))
        build = await service.build_create(bundle_id, actor=EDITOR)
        _, data = await service.build_mrpack(bundle_id, build.number)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            index = json.loads(archive.read("modrinth.index.json"))
        return [f.filename for f in build.local_files], [f["path"] for f in index["files"]]

    local, paths = mongo(scenario)
    assert local == ["private.jar"]
    assert not any("private" in p for p in paths)


def test_a_build_rereads_its_versions_in_one_batch_and_sees_an_edit_in_place(mongo: MongoRunner) -> None:
    # M3: Modrinth lets an author edit a version where it is (its game versions, channel,
    # dependencies); the version list does not change, so only reading the versions again sees it.
    async def scenario() -> tuple[str, str, list[Any]]:
        api = RecordedModrinthAPI()
        service = ModpackService(modpackDB=ModpackDatabase(), modrinthDB=ModrinthDatabase(), modrinthAPI=cast(Any, api),
                                 loaderAPI=cast(Any, FakeLoaders()), audit=RecordingAudit())
        created = await service.bundle_create(ModpackBundle(
            name="pack", description="", version="26.3", loader=MinecraftLoaders.fabric), actor=EDITOR)
        bundle_id = str(created.id)
        await service.project_add(bundle_id, ["iris"], ProjectOrigin.pack, actor=EDITOR)
        first = await service.build_create(bundle_id, actor=EDITOR)
        took = next(e.version_id for e in first.lock.entries if e.project_id == IRIS)
        for version in api.versions:
            if version["id"] == took:
                version["game_versions"] = ["1.21.1"]  # the author moved it to another Minecraft
        api.calls.clear()
        second = await service.build_create(bundle_id, actor=EDITOR)
        again = next(e.version_id for e in second.lock.entries if e.project_id == IRIS)
        rereads = [c for c in api.calls if c[0] == "version_get_many" and took in c[1]]
        return took, again, rereads

    took, again, rereads = mongo(scenario)
    assert again != took and len(rereads) == 1


# Builds de prueba (O8 B1, d-7985ee-a99db7; user-walk W5, H4 en d-7985ee-3bb53b).


def test_a_test_build_is_numbered_but_never_moves_latest(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        service, bundle_id = await empty_bundle()
        await service.project_add(bundle_id, ["iris"], ProjectOrigin.pack, actor=EDITOR)
        await service.build_create(bundle_id, actor=EDITOR)
        test = await service.build_create(bundle_id, actor=EDITOR, test=True)
        stored = await service.bundle_get(bundle_id)
        return test.number, (await service.build_get(bundle_id, 2)).test, stored.latest_build

    assert mongo(scenario) == (2, True, 1)


def test_retrying_a_test_builds_job_never_moves_latest(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        service, bundle_id = await empty_bundle()
        await service.project_add(bundle_id, ["iris"], ProjectOrigin.pack, actor=EDITOR)
        await service.build_create(bundle_id, actor=EDITOR, test=True, job_id="j1")
        await service.build_create(bundle_id, actor=EDITOR, test=True, job_id="j1")  # the retry
        stored = await service.bundle_get(bundle_id)
        return [b.number for b in await service.build_find(bundle_id)], stored.latest_build

    assert mongo(scenario) == ([1], None)


def test_a_test_build_is_announced_as_a_test(mongo: MongoRunner) -> None:
    from tests.test_notify import Recorder

    async def scenario() -> list[str]:
        recorder = Recorder()
        service = ModpackService(modpackDB=ModpackDatabase(), modrinthDB=ModrinthDatabase(),
                                 modrinthAPI=cast(Any, RecordedModrinthAPI()), loaderAPI=cast(Any, FakeLoaders()), audit=RecordingAudit(),
                                 notifier=cast(Any, recorder))
        bundle = await service.bundle_create(ModpackBundle(name="Survival", description="", version="26.3",
                                                           loader=MinecraftLoaders.fabric), actor=EDITOR)
        await service.project_add(str(bundle.id), ["iris"], ProjectOrigin.pack, actor=EDITOR)
        await service.build_create(str(bundle.id), actor=EDITOR, test=True)
        return recorder.sent

    [message] = mongo(scenario)
    assert message.startswith("🧪 Survival: build de prueba 1 creado, ") and message.endswith(" archivos.")


async def _normal_test_normal() -> tuple[ModpackService, str]:
    """Builds 1 (normal), 2 (test) and 3 (normal) of one pack."""
    service, bundle_id = await empty_bundle()
    await service.project_add(bundle_id, ["iris"], ProjectOrigin.pack, actor=EDITOR)
    await service.build_create(bundle_id, actor=EDITOR)
    await service.build_create(bundle_id, actor=EDITOR, test=True)
    return service, bundle_id


def test_approving_moves_latest_and_only_forward(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        service, bundle_id = await _normal_test_normal()
        first = await service.build_approve(bundle_id, 2, EDITOR)
        latest_then = (await service.bundle_get(bundle_id)).latest_build
        await service.build_create(bundle_id, actor=EDITOR, test=True)  # 3, a test
        await service.build_create(bundle_id, actor=EDITOR)  # 4
        old = await service.build_approve(bundle_id, 3, EDITOR)
        return (first.approved, latest_then, (await service.build_get(bundle_id, 2)).test,
                old.approved, old.in_force, old.newer, (await service.bundle_get(bundle_id)).latest_build)

    assert mongo(scenario) == (True, 2, False, True, 4, [4], 4)


def test_approve_and_recommend_does_both(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        service, bundle_id = await _normal_test_normal()
        result = await service.build_approve(bundle_id, 2, EDITOR, recommend=True)
        return result.bundle.recommended_build, result.in_force, (await service.build_get(bundle_id, 2)).test

    assert mongo(scenario) == (2, 2, False)


def test_approving_twice_is_a_no_op_the_second_time(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        service, bundle_id = await _normal_test_normal()
        service.audit = audit = RecordingAudit()
        first = await service.build_approve(bundle_id, 2, EDITOR)
        second = await service.build_approve(bundle_id, 2, EDITOR)
        return first.approved, second.approved, [e[0] for e in audit.events].count("build.approve")

    assert mongo(scenario) == (True, False, 1)


def test_recommending_a_test_is_refused(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        service, bundle_id = await _normal_test_normal()
        with pytest.raises(HTTPException) as refused:
            await service.build_recommend(bundle_id, 2, EDITOR)
        return refused.value.status_code, refused.value.detail, (await service.bundle_get(bundle_id)).recommended_build

    # In Spanish: the panel shows it as it is (final review).
    assert mongo(scenario) == (409, "El build 2 es una prueba: apruébalo primero.", None)


def test_an_approved_build_is_announced(mongo: MongoRunner) -> None:
    from tests.test_notify import Recorder

    async def scenario() -> list[str]:
        service, bundle_id = await _normal_test_normal()
        recorder = Recorder()
        service.notifier = cast(Any, recorder)
        await service.build_approve(bundle_id, 2, EDITOR)
        await service.build_approve(bundle_id, 2, EDITOR)  # nothing new: no second message
        return recorder.sent

    assert mongo(scenario) == ["📦 pack: build 2 aprobado."]


# Nombre y estrella (O8 B2, B3; user-walk W13, W14).


def test_naming_a_build_never_rewrites_its_lock(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        service, bundle_id = await empty_bundle()
        await service.project_add(bundle_id, ["iris"], ProjectOrigin.pack, actor=EDITOR)
        before = await service.build_create(bundle_id, actor=EDITOR)
        await service.build_name(bundle_id, 1, "  con Iris 1.7 ", EDITOR)
        await service.build_star(bundle_id, 1, True, EDITOR)
        after = await service.build_get(bundle_id, 1)
        with pytest.raises(ValueError):
            await ModpackDatabase().build_set(bundle_id, 1, {"lock": None})
        # Read back from MongoDB on both sides: dates come naive and to the millisecond (final review).
        stored = await ModpackDatabase().build_find(bundle_id)
        return (after.name, after.starred, after.lock == before.lock, after.request == before.request,
                after.created_at == stored[0].created_at)

    assert mongo(scenario) == ("con Iris 1.7", True, True, True, True)


def test_starring_twice_stays_starred(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[bool, bool]:
        service, bundle_id = await empty_bundle()
        await service.project_add(bundle_id, ["iris"], ProjectOrigin.pack, actor=EDITOR)
        await service.build_create(bundle_id, actor=EDITOR)
        await service.build_star(bundle_id, 1, True, EDITOR)
        twice = (await service.build_star(bundle_id, 1, True, EDITOR)).starred
        return twice, (await service.build_star(bundle_id, 1, False, EDITOR)).starred

    assert mongo(scenario) == (True, False)
