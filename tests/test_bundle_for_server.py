"""Un modpack nuevo para un servidor sin modpack (diseño de usabilidad del panel §2).

- `deduction`: el loader y la versión de Minecraft que dicen los jars identificados del último
  escaneo, leídos del catálogo; None sin escaneo.
- `bundle_for_server`: de operador. Crea el bundle, lo vincula y, si se pide, importa el servidor,
  con un evento de auditoría por paso. Un servidor que ya tiene modpack es un 409 antes de crear
  nada. Si la importación falla, el pack queda creado y vinculado, y la respuesta lo dice.
Contra MongoDB real, salvo el rechazo al editor, que ocurre antes de tocar nada.
"""
from typing import Any, cast

import pytest
from fastapi import HTTPException

from src.database.modpack import ModpackDatabase
from src.database.pterodactyl import PterodactylDatabase
from src.model.modpack import ModpackBundle
from src.model.modpack.project import PackProject, ProjectOrigin
from src.model.utils import MinecraftLoaders
from src.services.pterodactyl import PterodactylService
from tests._actors import EDITOR, OPERATOR, RecordingAudit
from tests._modrinth import FABRIC_API, IRIS, SODIUM
from tests.conftest import MongoRunner
from tests.test_import import SearchingModrinthAPI
from tests.test_server_scan import T0, FakePanel, setup, sha1

# Grabadas: Fabric API y Iris son fabric para 26.3; esta de Sodium, fabric para 1.21.11.
FABRIC_API_V, IRIS_V, SODIUM_V = "bNnaTiuM", "vTN4NRGW", "RB7CDjTS"


async def scanned_server(audit: RecordingAudit) -> tuple[PterodactylService, str]:
    """Un servidor sin modpack, escaneado: tres jars que Modrinth conoce y uno que no."""
    modrinth = SearchingModrinthAPI({sha1(b"fabric api"): FABRIC_API_V, sha1(b"iris jar"): IRIS_V,
                                     sha1(b"sodium jar"): SODIUM_V})
    panel = FakePanel()
    panel.files = {"fabric-api.jar": (b"fabric api", T0), "iris.jar": (b"iris jar", T0),
                   "sodium.jar": (b"sodium jar", T0), "privado.jar": (b"opaque", T0)}
    service, server_id = await setup(panel, modrinth, audit)
    await service.scan(server_id, actor=None)
    return service, server_id


@pytest.mark.network
def test_the_deduction_reads_the_identified_jars_from_the_catalog(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        service, server_id = await scanned_server(RecordingAudit())
        return await service.deduction(server_id)

    deduction = mongo(scenario)
    assert (deduction.loader, deduction.game_version, deduction.identified) == ("fabric", "26.3", 3)
    assert deduction.loader_votes == {"fabric": 3} and deduction.version_votes == {"26.3": 2, "1.21.11": 1}


@pytest.mark.network
def test_without_a_scan_there_is_no_deduction(mongo: MongoRunner) -> None:
    async def scenario() -> Any:
        service, server_id = await setup(FakePanel(), SearchingModrinthAPI({}))
        return await service.deduction(server_id)

    assert mongo(scenario) is None


@pytest.mark.network
def test_operator_creates_links_and_imports_with_audit_in_order(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        audit = RecordingAudit()
        service, server_id = await scanned_server(audit)
        result = await service.bundle_for_server(server_id, "Creativo", "26.3", MinecraftLoaders.fabric, True, OPERATOR)
        # T1: the import is the server's import job, which the worker runs.
        from src.database.jobs import JobDatabase
        from src.services.jobs import JobWorker
        worker = JobWorker(JobDatabase(), service.handlers(), owner="w1")
        while await worker.run_once():
            pass
        server = await PterodactylDatabase().server_get(server_id)
        stored = await ModpackBundle.get(result.bundle.id)
        assert stored is not None
        return (result, server.bundle_id, [(e[0], e[1].kind, e[1].id) for e in audit.events],
                sorted(p.project_id for p in stored.projects), [f.filename for f in stored.local_files])

    result, linked, events, projects, local_files = mongo(scenario)
    bundle_id = str(result.bundle.id)
    assert (result.bundle.name, result.bundle.version, result.bundle.loader) == ("Creativo", "26.3", MinecraftLoaders.fabric)
    assert linked == bundle_id
    assert [e[0] for e in events] == ["bundle.create", "server.bundle", "server.import.request", "server.import"]
    assert events[0][1:] == ("bundle", bundle_id)
    assert result.imported is None and result.import_error is None
    assert result.import_job is not None and result.import_job.kind == "server_import"
    assert projects == sorted([FABRIC_API, IRIS, SODIUM]) and local_files == ["privado.jar"]


@pytest.mark.network
def test_without_import_the_pack_is_created_and_linked_only(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, list[str]]:
        audit = RecordingAudit()
        service, server_id = await scanned_server(audit)
        result = await service.bundle_for_server(server_id, "Vacío", "26.3", MinecraftLoaders.fabric, False, OPERATOR)
        return result, [e[0] for e in audit.events]

    result, events = mongo(scenario)
    assert result.imported is None and result.import_error is None
    assert events == ["bundle.create", "server.bundle"]


def test_editor_is_refused() -> None:
    import asyncio

    nothing = cast(Any, None)
    service = PterodactylService(pterodactylAPI=nothing, PterodactylDB=nothing, modrinthAPI=nothing,
                                 modrinthDB=nothing, modpackDB=nothing, audit=nothing)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(service.bundle_for_server("x", "Creativo", "26.3", MinecraftLoaders.fabric, True, EDITOR))
    assert caught.value.status_code == 403


@pytest.mark.network
def test_a_linked_server_is_a_409_and_nothing_is_created(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[int, int, list[str]]:
        audit = RecordingAudit()
        service, server_id = await scanned_server(audit)
        existing = await ModpackDatabase().bundle_insert(ModpackBundle(
            name="Ya", description="", version="26.3", loader=MinecraftLoaders.fabric))
        await service.server_link_bundle(server_id, str(existing.id), OPERATOR)
        audit.events.clear()
        with pytest.raises(HTTPException) as caught:
            await service.bundle_for_server(server_id, "Otro", "26.3", MinecraftLoaders.fabric, True, OPERATOR)
        return caught.value.status_code, await ModpackBundle.count(), [e[0] for e in audit.events]

    assert mongo(scenario) == (409, 1, [])


@pytest.mark.network
def test_import_failure_leaves_the_pack_linked_and_says_so(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, Any, list[str]]:
        audit = RecordingAudit()
        service, server_id = await scanned_server(audit)

        async def broken(server_id: str, actor: Any) -> Any:
            raise RuntimeError("panel down")

        service.import_request = broken  # type: ignore[method-assign, assignment]
        result = await service.bundle_for_server(server_id, "Creativo", "26.3", MinecraftLoaders.fabric, True, OPERATOR)
        server = await PterodactylDatabase().server_get(server_id)
        return result, server.bundle_id, [e[0] for e in audit.events]

    result, linked, events = mongo(scenario)
    assert linked == str(result.bundle.id)
    assert result.imported is None
    assert result.import_error is not None and "importación" in result.import_error
    assert "panel down" not in result.import_error
    assert events == ["bundle.create", "server.bundle"]


async def racing_service(audit: RecordingAudit) -> tuple[PterodactylService, str, str]:
    """A server without a pack whose link another operator takes right after this pack is created
    (d-7985ee-b8e927): the insert links another pack before returning."""
    service, server_id = await scanned_server(audit)
    other = await ModpackDatabase().bundle_insert(ModpackBundle(
        name="Del otro", description="", version="26.3", loader=MinecraftLoaders.fabric))
    insert = service.modpackDB.bundle_insert

    async def racing_insert(bundle: ModpackBundle) -> ModpackBundle:
        created = await insert(bundle)
        await PterodactylDatabase().server_set_bundle(server_id, str(other.id))
        return created

    service.modpackDB.bundle_insert = racing_insert  # type: ignore[method-assign]
    return service, server_id, str(other.id)


@pytest.mark.network
def test_a_lost_race_deletes_the_new_pack_and_keeps_the_other_link(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        audit = RecordingAudit()
        service, server_id, other = await racing_service(audit)
        with pytest.raises(HTTPException) as caught:
            await service.bundle_for_server(server_id, "Creativo", "26.3", MinecraftLoaders.fabric, True, OPERATOR)
        server = await PterodactylDatabase().server_get(server_id)
        names = sorted(b.name for b in await ModpackBundle.find_all().to_list())
        return caught.value, server.bundle_id == other, names, [e[0] for e in audit.events], audit.events[-1][1]

    error, kept, names, events, deleted = mongo(scenario)
    assert error.status_code == 409
    assert "otro operador" in error.detail and "«Creativo»" in error.detail and "se borró" in error.detail
    assert kept and names == ["Del otro"]
    assert events == ["bundle.create", "bundle.delete"]
    # The pack is gone by the time it is recorded: the event carries the name itself (A10).
    assert deleted.name == "Creativo"


@pytest.mark.network
def test_a_lost_race_whose_delete_fails_still_says_what_happened(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        audit = RecordingAudit()
        service, server_id, other = await racing_service(audit)

        async def broken(bundle_id: str) -> bool:
            raise RuntimeError("mongo hiccup")

        service.modpackDB.bundle_delete_empty = broken  # type: ignore[method-assign]
        with pytest.raises(HTTPException) as caught:
            await service.bundle_for_server(server_id, "Creativo", "26.3", MinecraftLoaders.fabric, True, OPERATOR)
        names = sorted(b.name for b in await ModpackBundle.find_all().to_list())
        return caught.value, names, [e[0] for e in audit.events]

    error, names, events = mongo(scenario)
    assert error.status_code == 409 and "otro operador" in error.detail and "no se pudo borrar" in error.detail
    assert names == ["Creativo", "Del otro"]
    assert events == ["bundle.create"]


@pytest.mark.network
def test_deleting_an_empty_pack_refuses_one_with_projects_or_builds(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[bool, bool, bool, int]:
        db = ModpackDatabase()
        empty = await db.bundle_insert(ModpackBundle(name="a", description="", version="26.3", loader=MinecraftLoaders.fabric))
        built = await db.bundle_insert(ModpackBundle(name="b", description="", version="26.3", loader=MinecraftLoaders.fabric,
                                                     latest_build=1))
        used = await db.bundle_insert(ModpackBundle(name="c", description="", version="26.3", loader=MinecraftLoaders.fabric))
        await db.bundle_projects_add(str(used.id), [PackProject(project_id=SODIUM, origin=ProjectOrigin.admin)])
        return (await db.bundle_delete_empty(str(empty.id)), await db.bundle_delete_empty(str(built.id)),
                await db.bundle_delete_empty(str(used.id)), await ModpackBundle.count())

    deleted, deleted_built, deleted_used, left = mongo(scenario)
    assert deleted and not deleted_built and not deleted_used and left == 2


@pytest.mark.network
def test_deleting_a_pack_archives_its_notes_and_no_other_packs(mongo: MongoRunner) -> None:
    """N18 of the notes' user-walk: a deleted pack left its notes live, reachable from the knowledge base by id.
    They are archived with it (deleting is archiving, D11); another pack's notes are left as they are."""
    from datetime import datetime, timezone
    from src.database.note import NoteDatabase
    from src.model.modpack.note import VersionNote
    from tests._actors import EDITOR

    async def scenario() -> tuple[bool, list[tuple[str, bool, int]]]:
        db, notes = ModpackDatabase(), NoteDatabase()
        gone = await db.bundle_insert(ModpackBundle(name="a", description="", version="26.3", loader=MinecraftLoaders.fabric))
        kept = await db.bundle_insert(ModpackBundle(name="b", description="", version="26.3", loader=MinecraftLoaders.fabric))
        for bundle, key in ((gone, "k1"), (kept, "k2")):
            await notes.insert(VersionNote(bundle_id=str(bundle.id), project_id=SODIUM, version_id="v", verdict="fails",
                                           text="x", author=EDITOR.ref(), at=datetime.now(timezone.utc), key=key))
        deleted = await db.bundle_delete_empty(str(gone.id))
        found = await VersionNote.find_all().sort("key").to_list()
        return deleted, [(n.key, n.archived, n.rev) for n in found]

    deleted, found = mongo(scenario)
    assert deleted and found == [("k1", True, 1), ("k2", False, 0)]
