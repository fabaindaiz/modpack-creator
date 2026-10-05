"""El PATCH de un bundle escribe solo las claves que manda (decisión del dueño, 2026-10-05).

Antes, `pins`, `replacements` y `server_decisions` se reemplazaban enteros: mandar un pin borraba
los demás. Ahora cada clave se escribe sola, como `pin_set`; en `pins` y `server_decisions` un
`null` borra la clave (RFC 7396). En `replacements`, `null` sigue queriendo decir "quitar el mod",
así que la regla de un pack se borra con su propia ruta. Contra MongoDB real."""
from typing import Any, cast

import pytest
from fastapi import HTTPException

from src.database.modpack import ModpackDatabase
from src.database.modrinth import ModrinthDatabase
from src.model.modpack import ModpackBundle, ModpackBundlePatch
from src.model.utils import MinecraftLoaders
from src.services.modpack import ModpackService
from tests._actors import EDITOR, RecordingAudit
from tests.conftest import MongoRunner
from tests.test_builds import FakeLoaders

pytestmark = pytest.mark.network


async def bundle(**fields: Any) -> tuple[ModpackService, str, RecordingAudit]:
    audit = RecordingAudit()
    service = ModpackService(modpackDB=ModpackDatabase(), modrinthDB=ModrinthDatabase(), modrinthAPI=cast(Any, None),
                             loaderAPI=cast(Any, FakeLoaders()), audit=audit)
    created = await service.bundle_create(ModpackBundle(
        name="pack", description="", version="26.3", loader=MinecraftLoaders.fabric, **fields), actor=EDITOR)
    return service, str(created.id), audit


async def stored(bundle_id: str) -> ModpackBundle:
    return await ModpackDatabase().bundle_get(bundle_id)


def test_a_pin_sent_keeps_the_other_pins(mongo: MongoRunner) -> None:
    async def scenario() -> dict[str, str]:
        service, bundle_id, _ = await bundle(pins={"AAAA": "a1", "BBBB": "b1"})
        await service.bundle_update(bundle_id, ModpackBundlePatch(pins={"AAAA": "a2", "CCCC": "c1"}), EDITOR)
        return (await stored(bundle_id)).pins

    assert mongo(scenario) == {"AAAA": "a2", "BBBB": "b1", "CCCC": "c1"}


def test_null_removes_a_pin_or_a_decision_and_leaves_the_rest(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[dict[str, str], dict[str, bool]]:
        service, bundle_id, _ = await bundle(pins={"AAAA": "a1", "BBBB": "b1"},
                                             server_decisions={"XXXX": True, "YYYY": False})
        await service.bundle_update(bundle_id, ModpackBundlePatch.model_validate(
            {"pins": {"BBBB": None}, "server_decisions": {"YYYY": None, "ZZZZ": True}}), EDITOR)
        after = await stored(bundle_id)
        return after.pins, after.server_decisions

    assert mongo(scenario) == ({"AAAA": "a1"}, {"XXXX": True, "ZZZZ": True})


def test_a_replacement_sent_keeps_the_others_and_null_still_drops_the_mod(mongo: MongoRunner) -> None:
    async def scenario() -> dict[str, Any]:
        service, bundle_id, _ = await bundle(replacements={"XXXX": "yyyy"})
        await service.bundle_update(bundle_id, ModpackBundlePatch.model_validate(
            {"replacements": {"ZZZZ": None}}), EDITOR)
        return (await stored(bundle_id)).replacements

    assert mongo(scenario) == {"XXXX": "yyyy", "ZZZZ": None}


def test_the_plain_fields_still_change(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[str, MinecraftLoaders, Any, list[str], dict[str, str]]:
        service, bundle_id, audit = await bundle(loader_version="0.17.0", pins={"AAAA": "a1"})
        await service.bundle_update(bundle_id, ModpackBundlePatch.model_validate(
            {"name": "renamed", "loader": "neoforge", "loader_version": None, "channels": ["release", "beta"]}), EDITOR)
        after = await stored(bundle_id)
        assert [event[0] for event in audit.events] == ["bundle.create", "bundle.update"]
        return after.name, after.loader, after.loader_version, after.channels, after.pins

    assert mongo(scenario) == ("renamed", MinecraftLoaders.neoforge, None, ["release", "beta"], {"AAAA": "a1"})


def test_an_empty_patch_changes_nothing(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[str, dict[str, str]]:
        service, bundle_id, _ = await bundle(pins={"AAAA": "a1"})
        updated = await service.bundle_update(bundle_id, ModpackBundlePatch(), EDITOR)
        return updated.name, updated.pins

    assert mongo(scenario) == ("pack", {"AAAA": "a1"})


@pytest.mark.parametrize("key", ["a.b", "$where", "", "x y"])
def test_a_key_that_is_not_a_modrinth_id_is_refused_and_nothing_is_written(mongo: MongoRunner, key: str) -> None:
    async def scenario() -> tuple[int, str, dict[str, str]]:
        service, bundle_id, audit = await bundle(pins={"AAAA": "a1"})
        with pytest.raises(HTTPException) as error:
            await service.bundle_update(bundle_id, ModpackBundlePatch(name="renamed", pins={key: "v"}), EDITOR)
        assert [event[0] for event in audit.events] == ["bundle.create"]
        after = await stored(bundle_id)
        return error.value.status_code, after.name, after.pins

    assert mongo(scenario) == (422, "pack", {"AAAA": "a1"})


@pytest.mark.parametrize("field", ["name", "description", "version", "loader", "channels", "pins",
                                   "replacements", "server_decisions"])
def test_null_on_a_field_the_bundle_needs_is_refused(mongo: MongoRunner, field: str) -> None:
    # Guardar `name: null` dejaría un bundle que ya no carga: cada lectura daría 500.
    async def scenario() -> tuple[int, str]:
        service, bundle_id, _ = await bundle()
        with pytest.raises(HTTPException) as error:
            await service.bundle_update(bundle_id, ModpackBundlePatch.model_validate({field: None}), EDITOR)
        return error.value.status_code, (await stored(bundle_id)).name

    assert mongo(scenario) == (422, "pack")


def test_patching_a_missing_bundle_is_404(mongo: MongoRunner) -> None:
    async def scenario() -> int:
        service, _, _ = await bundle()
        with pytest.raises(HTTPException) as error:
            await service.bundle_update("0" * 24, ModpackBundlePatch(pins={"AAAA": "a1"}), EDITOR)
        return error.value.status_code

    assert mongo(scenario) == 404


def test_deleting_a_pack_rule_removes_only_that_rule(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[dict[str, Any], list[tuple[str, Any]]]:
        service, bundle_id, audit = await bundle(replacements={"XXXX": "yyyy", "ZZZZ": None})
        await service.bundle_replacement_delete(bundle_id, "ZZZZ", EDITOR)
        return (await stored(bundle_id)).replacements, [(e[0], e[2]) for e in audit.events[1:]]

    assert mongo(scenario) == ({"XXXX": "yyyy"}, [("bundle.replacement.delete", {"project_id": "ZZZZ"})])


@pytest.mark.parametrize("project_id", ["NONE", "a.b"])
def test_deleting_a_rule_the_pack_does_not_have_is_refused(mongo: MongoRunner, project_id: str) -> None:
    async def scenario() -> tuple[int, dict[str, Any]]:
        service, bundle_id, _ = await bundle(replacements={"XXXX": "yyyy"})
        with pytest.raises(HTTPException) as error:
            await service.bundle_replacement_delete(bundle_id, project_id, EDITOR)
        return error.value.status_code, (await stored(bundle_id)).replacements

    status, after = mongo(scenario)
    assert after == {"XXXX": "yyyy"}
    assert status == (404 if project_id == "NONE" else 422)
