"""Importar un servidor: sus jars ajenos al bundle (i-7985ee-17745b, d-7985ee-c8a15d).

- Identificado por hash y ausente del bundle: entra como del admin.
- Desconocido: se lee su metadata (como dato) y se buscan candidatos en Modrinth por id y por
  nombre; no entra hasta que el admin confirme. Confirmado, el jar queda reclamado por ese proyecto
  y la comparación lo trata como una versión distinta a reemplazar.
- Sin candidatos (o el admin lo decide): archivo local del bundle, por su sha512.
Contra MongoDB real; el panel es un doble con jars sintéticos, y Modrinth contesta con datos
grabados.
"""
import copy
import io
import json
import zipfile
from typing import Any

import pytest
from fastapi import HTTPException

from src.database.modpack import ModpackDatabase
from src.model.modpack import ModpackBundle
from src.model.modrinth import ModrinthProject
from tests._actors import OPERATOR, RecordingAudit
from tests._modrinth import FABRIC_API, IRIS
from tests.conftest import MongoRunner
from tests.test_server_scan import T0, FakePanel, HashModrinthAPI, build_with_fabric_api, setup, sha1

pytestmark = pytest.mark.network

LITHIUM = "gvQqBUqZ"


def fabric_jar(mod_id: str, name: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("fabric.mod.json", json.dumps({"schemaVersion": 1, "id": mod_id, "name": name, "version": "1"}))
    return buffer.getvalue()


class SearchingModrinthAPI(HashModrinthAPI):
    """Además del hash: proyectos por slug o id (404 si no existe) y una búsqueda por nombre."""

    async def project_get(self, slug: str) -> ModrinthProject:
        if slug not in self.projects:
            raise HTTPException(status_code=404, detail="not found")
        return ModrinthProject.parse(**copy.deepcopy(self.projects[slug]))

    async def search(self, query: str, facets: list[list[str]], limit: int = 5) -> list[dict[str, Any]]:
        self.calls.append(("search", (query, json.dumps(facets))))
        hits = [p for p in {p["id"]: p for p in self.projects.values()}.values()
                if query.lower() in p["title"].lower() and p["project_type"] == "mod"]
        return [{"project_id": p["id"], "slug": p["slug"], "title": p["title"], "project_type": p["project_type"]}
                for p in hits[:limit]]


async def linked_server(files: dict[str, bytes], iris_channel: str = "release",
                        audit: Any = None) -> tuple[Any, str, str]:
    """Un servidor asociado a un bundle con Fabric API, escaneado con estos jars. `iris_channel` es
    el canal de la versión de Iris que el servidor tiene instalada."""
    modrinth = SearchingModrinthAPI({})
    bundle_id, version_id = await build_with_fabric_api(modrinth)
    modrinth.known[sha1(b"fabric api")] = version_id
    iris = next(v for v in modrinth.versions if v["project_id"] == IRIS)
    iris["version_type"] = iris_channel
    modrinth.known[sha1(b"iris jar")] = iris["id"]
    panel = FakePanel()
    panel.files = {name: (data, T0) for name, data in files.items()}
    service, server_id = await setup(panel, modrinth, audit)
    await service.server_link_bundle(server_id, bundle_id, actor=OPERATOR)
    await service.scan(server_id, actor=None)
    return service, server_id, bundle_id


def test_importing_sorts_every_jar_the_bundle_does_not_explain(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        service, server_id, bundle_id = await linked_server({
            "fabric-api.jar": b"fabric api",                        # ya en el bundle
            "iris.jar": b"iris jar",                                 # identificado por hash, ajeno
            "lithium-dev.jar": fabric_jar("lithium", "Lithium"),    # desconocido; su id es un slug
            "privado.jar": fabric_jar("privado", "Mod Privado"),    # desconocido; no está en Modrinth
            "roto.jar": b"not a zip at all",                         # desconocido; ni metadata
        })
        result = await service.import_server(server_id, actor=OPERATOR)
        bundle = await ModpackBundle.get(bundle_id)
        assert bundle is not None
        return (result.added, [(c.path, [x.project_id for x in c.candidates]) for c in result.candidates],
                sorted(result.local), {p.project_id: p.origin.value for p in bundle.projects},
                sorted(f.filename for f in bundle.local_files))

    added, candidates, local, projects, local_files = mongo(scenario)
    assert added == [IRIS]
    assert candidates == [("/mods/lithium-dev.jar", [LITHIUM])]
    assert local == ["/mods/privado.jar", "/mods/roto.jar"]
    assert projects == {FABRIC_API: "pack", IRIS: "admin"}
    assert local_files == ["privado.jar", "roto.jar"]


def test_importing_twice_adds_nothing_new(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[list[str], list[str], int]:
        service, server_id, bundle_id = await linked_server({"iris.jar": b"iris jar", "privado.jar": b"opaque"})
        await service.import_server(server_id, actor=OPERATOR)
        again = await service.import_server(server_id, actor=OPERATOR)
        bundle = await ModpackBundle.get(bundle_id)
        assert bundle is not None
        return again.added, again.local, len(bundle.local_files)

    assert mongo(scenario) == ([], [], 1)


def test_a_confirmed_candidate_joins_the_bundle_and_claims_its_jar(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[dict[str, str], list[str], list[str]]:
        service, server_id, bundle_id = await linked_server({"fabric-api.jar": b"fabric api",
                                                             "lithium-dev.jar": fabric_jar("lithium", "Lithium")})
        await service.import_server(server_id, actor=OPERATOR)
        await service.import_confirm(server_id, "/mods/lithium-dev.jar", LITHIUM, actor=OPERATOR)
        bundle = await ModpackBundle.get(bundle_id)
        assert bundle is not None
        # Contra el build 1 (sin Lithium), el jar reclamado sobra en vez de ser desconocido.
        result = await service.compare(server_id, build=1)
        return ({p.project_id: p.origin.value for p in bundle.projects}, [f.path for f in result.extra],
                [f.path for f in result.unknown])

    projects, extra, unknown = mongo(scenario)
    assert projects == {FABRIC_API: "pack", LITHIUM: "admin"}
    assert extra == ["/mods/lithium-dev.jar"] and unknown == []


def test_a_candidate_the_admin_rejects_becomes_a_local_file(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[list[str], bool]:
        service, server_id, bundle_id = await linked_server({"fabric-api.jar": b"fabric api",
                                                             "lithium-dev.jar": fabric_jar("lithium", "Lithium")})
        await service.import_server(server_id, actor=OPERATOR)
        await service.import_local(server_id, "/mods/lithium-dev.jar", actor=OPERATOR)
        bundle = await ModpackBundle.get(bundle_id)
        assert bundle is not None
        return [f.filename for f in bundle.local_files], (await service.compare(server_id, build=1)).in_sync

    local, in_sync = mongo(scenario)
    # El build 1 no tiene el archivo local (se registró después): no coincide hasta otro build.
    assert local == ["lithium-dev.jar"] and in_sync is False


def test_importing_needs_a_linked_server(mongo: MongoRunner) -> None:
    async def scenario() -> list[int]:
        service, server_id = await setup(FakePanel(), SearchingModrinthAPI({}))
        codes = []
        for call in (lambda: service.import_server(server_id, actor=OPERATOR),
                     lambda: service.import_confirm(server_id, "/mods/x.jar", LITHIUM, actor=OPERATOR),
                     lambda: service.import_local(server_id, "/mods/x.jar", actor=OPERATOR)):
            with pytest.raises(HTTPException) as refused:
                await call()
            codes.append(refused.value.status_code)
        return codes

    assert mongo(scenario) == [409, 409, 409]


# Lo que corre en el servidor (dueño, 2026-10-09, por la observación de un tester): un mod ambiguo
# salía "Por decidir" y uno con solo beta "con problemas", aunque los dos estaban corriendo.


def test_importing_records_that_every_identified_mod_runs_on_the_server(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[dict[str, bool], list[Any]]:
        audit = RecordingAudit()
        service, server_id, bundle_id = await linked_server({"fabric-api.jar": b"fabric api", "iris.jar": b"iris jar"},
                                                            audit=audit)
        await service.import_server(server_id, actor=OPERATOR)
        bundle = await ModpackBundle.get(bundle_id)
        assert bundle is not None
        return bundle.server_decisions, [e[2] for e in audit.events if e[0] == "server.import"]

    decisions, [details] = mongo(scenario)
    # Fabric API was already in the bundle: importing again reaches it too.
    assert decisions == {FABRIC_API: True, IRIS: True}
    assert sorted(details["on_server"]) == sorted([FABRIC_API, IRIS])


def test_importing_never_overrides_a_decision_the_admin_made(mongo: MongoRunner) -> None:
    async def scenario() -> dict[str, bool]:
        service, server_id, bundle_id = await linked_server({"iris.jar": b"iris jar"})
        await ModpackDatabase().bundle_decision_set(bundle_id, IRIS, False)
        await service.import_server(server_id, actor=OPERATOR)
        bundle = await ModpackBundle.get(bundle_id)
        assert bundle is not None
        return bundle.server_decisions

    assert mongo(scenario) == {IRIS: False}


@pytest.mark.parametrize("channel, expected", [("beta", {IRIS: "beta"}), ("alpha", {IRIS: "alpha"}), ("release", {})])
def test_a_beta_running_on_the_server_becomes_a_channel_exception(
        mongo: MongoRunner, channel: str, expected: dict[str, str]) -> None:
    async def scenario() -> tuple[dict[str, str], list[Any], Any]:
        audit = RecordingAudit()
        service, server_id, bundle_id = await linked_server({"iris.jar": b"iris jar"}, iris_channel=channel, audit=audit)
        result = await service.import_server(server_id, actor=OPERATOR)
        bundle = await ModpackBundle.get(bundle_id)
        assert bundle is not None
        return bundle.channel_exceptions, [e[2] for e in audit.events if e[0] == "server.import"], result

    exceptions, [details], result = mongo(scenario)
    assert exceptions == expected and details["channel_exceptions"] == expected
    # B5: the report says what it marked (owner, 2026-10-09).
    assert result.channel_exceptions == expected and result.on_server == [IRIS]


def test_importing_keeps_an_exception_the_admin_already_set(mongo: MongoRunner) -> None:
    async def scenario() -> dict[str, str]:
        service, server_id, bundle_id = await linked_server({"iris.jar": b"iris jar"}, iris_channel="beta")
        await ModpackDatabase().bundle_channel_exception_set(bundle_id, IRIS, "alpha")
        await service.import_server(server_id, actor=OPERATOR)
        bundle = await ModpackBundle.get(bundle_id)
        assert bundle is not None
        return bundle.channel_exceptions

    assert mongo(scenario) == {IRIS: "alpha"}



# T1 (docs/superpowers/plans/2026-10-09-pendientes.md): importar como trabajo, escaneando primero,
# sin volver a bajar jars y buscando en lote.


def test_importing_scans_first_and_downloads_no_jar_again(mongo: MongoRunner) -> None:
    # S1, S5, S2: the import's scan is incremental (nothing changed: nothing downloaded), the unknown
    # jars' metadata comes from the scan, and every slug goes in one Modrinth request; a text search
    # only for the jars whose slug found no mod.
    async def scenario() -> tuple[list[str], list[tuple[str, Any]], Any]:
        service, server_id, bundle_id = await linked_server({
            "fabric-api.jar": b"fabric api", "lithium-dev.jar": fabric_jar("lithium", "Lithium"),
            "privado.jar": fabric_jar("privado", "Mod Privado")})
        service.pterodactylAPI.calls.clear()
        service.modrinthAPI.calls.clear()
        result = await service.import_server(server_id, actor=OPERATOR)
        return service.pterodactylAPI.calls, service.modrinthAPI.calls, result

    panel_calls, modrinth_calls, result = mongo(scenario)
    assert [c for c in panel_calls if c.startswith("download")] == []
    assert [c[0] for c in modrinth_calls if c[0] in ("project_get", "project_get_many", "search")] == [
        "project_get_many", "search"]
    assert [c for c in modrinth_calls if c[0] == "search"][0][1][0] == "Mod Privado"
    assert [(c.path, [x.project_id for x in c.candidates]) for c in result.candidates] == [("/mods/lithium-dev.jar", [LITHIUM])]
    assert result.local == ["/mods/privado.jar"]


def test_importing_a_server_never_scanned_scans_it_first(mongo: MongoRunner) -> None:
    async def scenario() -> list[str]:
        modrinth = SearchingModrinthAPI({})
        bundle_id, version_id = await build_with_fabric_api(modrinth)
        panel = FakePanel()
        panel.files = {"iris.jar": (b"iris jar", T0)}
        modrinth.known[sha1(b"iris jar")] = next(v["id"] for v in modrinth.versions if v["project_id"] == IRIS)
        service, server_id = await setup(panel, modrinth)
        await service.server_link_bundle(server_id, bundle_id, actor=OPERATOR)
        return (await service.import_server(server_id, actor=OPERATOR)).added

    assert mongo(scenario) == [IRIS]


def test_a_scan_stored_before_metadata_reads_its_unknown_jars_once(mongo: MongoRunner) -> None:
    # S5 (owner, 2026-10-09): an older scan has no metadata; the import downloads those jars once and
    # keeps what it read, so the next import downloads nothing.
    from src.model.pterodactyl import ServerScan

    async def scenario() -> tuple[list[str], list[str]]:
        service, server_id, bundle_id = await linked_server({"lithium-dev.jar": fabric_jar("lithium", "Lithium")})
        await ServerScan.get_pymongo_collection().update_many(
            {}, {"$set": {"files.$[].metadata": None, "files.$[].metadata_read": False}})
        rounds = []
        for _ in range(2):
            service.pterodactylAPI.calls.clear()
            await service.import_server(server_id, actor=OPERATOR)
            rounds.append([c for c in service.pterodactylAPI.calls if c.startswith("download")])
        return rounds[0], rounds[1]

    first, second = mongo(scenario)
    assert first == ["download /mods/lithium-dev.jar"] and second == []


def test_an_import_says_how_far_it_is(mongo: MongoRunner) -> None:
    async def scenario() -> list[str]:
        service, server_id, bundle_id = await linked_server({"lithium-dev.jar": fabric_jar("lithium", "Lithium"),
                                                             "privado.jar": fabric_jar("privado", "Mod Privado")})
        seen: list[str] = []

        async def report(label: str, done: int, total: int) -> None:
            seen.append(label)

        await service.import_server(server_id, actor=OPERATOR, report=report)
        return list(dict.fromkeys(seen))

    assert mongo(scenario) == ["Buscando en Modrinth", "Agregando al modpack"]


def test_importing_is_one_job_per_server_and_its_pending_candidates_outlive_a_reload(mongo: MongoRunner) -> None:
    # S3 (owner, 2026-10-09): the candidates live in the last import job, minus what was decided.
    from src.database.jobs import JobDatabase
    from src.services.jobs import JobWorker

    async def scenario() -> tuple[Any, ...]:
        service, server_id, bundle_id = await linked_server({
            "lithium-dev.jar": fabric_jar("lithium", "Lithium"), "create.jar": fabric_jar("lithium", "Lithium Copy")})
        job = await service.import_request(server_id, OPERATOR)
        again = await service.import_request(server_id, OPERATOR)
        worker = JobWorker(JobDatabase(), service.handlers(), owner="w1")
        while await worker.run_once():
            pass
        before = await service.import_pending(server_id)
        await service.import_confirm(server_id, "/mods/lithium-dev.jar", LITHIUM, actor=OPERATOR)
        after = await service.import_pending(server_id)
        return job, again, before, after

    job, again, before, after = mongo(scenario)
    assert (job.kind, job.key) == ("server_import", f"server:{job.payload['server_id']}") and again.id == job.id
    assert before is not None and sorted(c.path for c in before.candidates) == ["/mods/create.jar", "/mods/lithium-dev.jar"]
    assert after is not None and [c.path for c in after.candidates] == ["/mods/create.jar"]


def test_importing_while_another_job_of_the_server_runs_is_the_409_it_gives(mongo: MongoRunner) -> None:
    # Code review (2026-10-12): a scan or an update holds the server's key; importing answered that
    # job as if it were the import, and nothing was imported.
    from fastapi import HTTPException

    from src.database.jobs import JobDatabase

    async def scenario() -> tuple[int, str]:
        service, server_id, _ = await linked_server({"lithium-dev.jar": fabric_jar("lithium", "Lithium")})
        await JobDatabase().enqueue("server_scan", key=f"server:{server_id}", payload={"server_id": server_id})
        with pytest.raises(HTTPException) as busy:
            await service.import_request(server_id, OPERATOR)
        return busy.value.status_code, str(busy.value.detail)

    status, detail = mongo(scenario)
    assert status == 409 and "active job" in detail
