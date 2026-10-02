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

from src.model.modpack import ModpackBundle
from src.model.modrinth import ModrinthProject
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


async def linked_server(files: dict[str, bytes]) -> tuple[Any, str, str]:
    """Un servidor asociado a un bundle con Fabric API, escaneado con estos jars."""
    modrinth = SearchingModrinthAPI({})
    bundle_id, version_id = await build_with_fabric_api(modrinth)
    modrinth.known[sha1(b"fabric api")] = version_id
    modrinth.known[sha1(b"iris jar")] = next(v["id"] for v in modrinth.versions if v["project_id"] == IRIS)
    panel = FakePanel()
    panel.files = {name: (data, T0) for name, data in files.items()}
    service, server_id = await setup(panel, modrinth)
    await service.server_link_bundle(server_id, bundle_id)
    await service.scan(server_id)
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
        result = await service.import_server(server_id)
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
        await service.import_server(server_id)
        again = await service.import_server(server_id)
        bundle = await ModpackBundle.get(bundle_id)
        assert bundle is not None
        return again.added, again.local, len(bundle.local_files)

    assert mongo(scenario) == ([], [], 1)


def test_a_confirmed_candidate_joins_the_bundle_and_claims_its_jar(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[dict[str, str], list[str], list[str]]:
        service, server_id, bundle_id = await linked_server({"fabric-api.jar": b"fabric api",
                                                             "lithium-dev.jar": fabric_jar("lithium", "Lithium")})
        await service.import_server(server_id)
        await service.import_confirm(server_id, "/mods/lithium-dev.jar", LITHIUM)
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
        await service.import_server(server_id)
        await service.import_local(server_id, "/mods/lithium-dev.jar")
        bundle = await ModpackBundle.get(bundle_id)
        assert bundle is not None
        return [f.filename for f in bundle.local_files], (await service.compare(server_id, build=1)).in_sync

    local, in_sync = mongo(scenario)
    # El build 1 no tiene el archivo local (se registró después): no coincide hasta otro build.
    assert local == ["lithium-dev.jar"] and in_sync is False


def test_importing_needs_a_linked_server_and_a_scan(mongo: MongoRunner) -> None:
    async def scenario() -> list[int]:
        service, server_id = await setup(FakePanel(), SearchingModrinthAPI({}))
        codes = []
        for call in (lambda: service.import_server(server_id),
                     lambda: service.import_confirm(server_id, "/mods/x.jar", LITHIUM),
                     lambda: service.import_local(server_id, "/mods/x.jar")):
            with pytest.raises(HTTPException) as refused:
                await call()
            codes.append(refused.value.status_code)
        return codes

    assert mongo(scenario) == [409, 409, 409]
