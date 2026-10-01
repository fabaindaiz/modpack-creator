"""Resolver un modpack guardado (i-7985ee-7f735a): el servicio arma el pedido desde el bundle,
trae de Modrinth lo que el lock pide y guarda lo traído en el catálogo. Contra MongoDB real y
respuestas grabadas de Modrinth (tests/_modrinth.py)."""
from typing import Any, cast

import pytest

from src.database.modpack import ModpackDatabase
from src.database.modrinth import ModrinthDatabase
from src.model.modpack import ModpackBundle, ModpackReplacement, ProjectOrigin
from src.model.modrinth import ModrinthVersion
from src.model.utils import MinecraftLoaders
from src.services.modpack import ModpackService
from tests._modrinth import IRIS, SHADER, SODIUM, RecordedModrinthAPI
from tests.conftest import MongoRunner

pytestmark = pytest.mark.network


async def bundle(api: RecordedModrinthAPI, *slugs: str, **fields: Any) -> tuple[ModpackService, str]:
    service = ModpackService(modpackDB=ModpackDatabase(), modrinthDB=ModrinthDatabase(), modrinthAPI=cast(Any, api))
    created = await service.bundle_create(ModpackBundle(
        name="pack", description="", version="26.3", loader=MinecraftLoaders.fabric, **fields))
    await service.project_add(str(created.id), list(slugs), ProjectOrigin.pack)
    return service, str(created.id)


def test_resolving_a_bundle_fetches_what_the_lock_needs_and_stores_it(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[dict[str, str], list[tuple[str, Any]], set[str]]:
        api = RecordedModrinthAPI()
        service, bundle_id = await bundle(api, "iris")
        api.calls.clear()
        lock = await service.resolve(bundle_id)
        assert lock.complete and not lock.problems
        stored = {v.version_id async for v in ModrinthVersion.find()}
        return {e.project_id: e.version_id for e in lock.entries}, api.calls, stored

    chosen, calls, stored = mongo(scenario)
    assert chosen[SODIUM] == "bAZQdGpg"
    # Iris: su tipo y sus versiones para 26.3 y fabric. Después, la versión exacta de Sodium que
    # fija, y el proyecto Sodium solo para saber su tipo, sin traer todas sus versiones.
    assert calls == [("project_get_many", (IRIS,)), ("project_versions", (IRIS, ("fabric",), ("26.3",))),
                     ("version_get_many", ("bAZQdGpg",)), ("project_get_many", (SODIUM,))]
    assert {chosen[IRIS], "bAZQdGpg"} <= stored


def test_a_shader_resolves_in_a_fabric_pack_and_stays_off_the_server(mongo: MongoRunner) -> None:
    async def scenario() -> dict[str, str]:
        service, bundle_id = await bundle(RecordedModrinthAPI(), "complementary-reimagined")
        lock = await service.resolve(bundle_id)
        assert not lock.problems
        return {e.project_id: e.server for e in lock.entries}

    assert mongo(scenario) == {SHADER: "exclude"}


def test_a_pack_rule_overrides_the_global_one(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[set[str], list[str]]:
        await ModpackDatabase().replacement_set(ModpackReplacement(project_id=SODIUM, replacement="embeddium"))
        service, bundle_id = await bundle(RecordedModrinthAPI(), "iris", replacements={SODIUM: None})
        lock = await service.resolve(bundle_id)
        return {e.project_id for e in lock.entries}, [p.kind for p in lock.problems]

    # La global cambiaría Sodium por embeddium, que no tiene versiones grabadas: daría un
    # problema "unresolvable". La del pack lo quita, sin problemas.
    assert mongo(scenario) == ({IRIS}, [])


def test_bundles_stored_before_resolution_fields_still_load(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[dict[str, Any], dict[str, str], list[str], list[Any]]:
        result = await ModpackBundle.get_pymongo_collection().insert_one(
            {"name": "old", "description": "", "version": "1.21.1", "loader": "fabric"})
        stored = await ModpackBundle.get(result.inserted_id)
        assert stored is not None
        return stored.replacements, stored.pins, stored.channels, stored.projects

    assert mongo(scenario) == ({}, {}, ["release"], [])
