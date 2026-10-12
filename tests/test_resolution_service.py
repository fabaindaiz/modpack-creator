"""Resolver un modpack guardado (i-7985ee-7f735a): el servicio arma el pedido desde el bundle,
trae de Modrinth lo que el lock pide y guarda lo traído en el catálogo. Contra MongoDB real y
respuestas grabadas de Modrinth (tests/_modrinth.py)."""
from typing import Any, cast

import pytest
from fastapi import HTTPException

from src.database.modpack import ModpackDatabase
from src.database.modrinth import ModrinthDatabase
from src.model.modpack import ModpackBundle, ModpackReplacement, ProjectOrigin
from src.model.modrinth import ModrinthVersion
from src.model.utils import MinecraftLoaders
from src.services.modpack import ModpackService
from tests._actors import EDITOR, RecordingAudit
from tests._modrinth import IRIS, SHADER, SODIUM, RecordedModrinthAPI
from tests.conftest import MongoRunner
from tests.test_builds import FakeLoaders

pytestmark = pytest.mark.network


async def bundle(api: RecordedModrinthAPI, *slugs: str, **fields: Any) -> tuple[ModpackService, str]:
    service = ModpackService(modpackDB=ModpackDatabase(), modrinthDB=ModrinthDatabase(), modrinthAPI=cast(Any, api),
                             loaderAPI=cast(Any, FakeLoaders()), audit=RecordingAudit())
    created = await service.bundle_create(ModpackBundle(
        name="pack", description="", version="26.3", loader=MinecraftLoaders.fabric, **fields), actor=EDITOR)
    await service.project_add(str(created.id), list(slugs), ProjectOrigin.pack, actor=EDITOR)
    return service, str(created.id)


def test_resolving_a_bundle_fetches_what_the_lock_needs_and_stores_it(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[dict[str, str], list[tuple[str, Any]], set[str]]:
        api = RecordedModrinthAPI()
        service, bundle_id = await bundle(api, "iris")
        api.calls.clear()
        lock = await service.resolve_fetching(bundle_id)
        assert lock.complete and not lock.problems
        stored = {v.version_id async for v in ModrinthVersion.find()}
        return {e.project_id: e.version_id for e in lock.entries}, api.calls, stored

    chosen, calls, stored = mongo(scenario)
    assert chosen[SODIUM] == "bAZQdGpg"
    # Iris: su tipo ya está en el catálogo (lo guardó project_add), así que solo sus versiones para
    # 26.3 y fabric (spec 2026-10-09-economia-de-requests). Después, la versión exacta de Sodium que
    # fija, y el proyecto Sodium solo para saber su tipo, sin traer todas sus versiones.
    assert calls == [("project_versions", (IRIS, ("fabric",), ("26.3",))),
                     ("version_get_many", ("bAZQdGpg",)), ("project_get_many", (SODIUM,))]
    assert {chosen[IRIS], "bAZQdGpg"} <= stored


def test_a_shader_resolves_in_a_fabric_pack_and_stays_off_the_server(mongo: MongoRunner) -> None:
    async def scenario() -> dict[str, str]:
        service, bundle_id = await bundle(RecordedModrinthAPI(), "complementary-reimagined")
        lock = await service.resolve_fetching(bundle_id)
        assert not lock.problems
        return {e.project_id: e.server for e in lock.entries}

    assert mongo(scenario) == {SHADER: "exclude"}


def test_a_pack_rule_overrides_the_global_one(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[set[str], list[str]]:
        await ModpackDatabase().replacement_set(ModpackReplacement(project_id=SODIUM, replacement="embeddium"))
        service, bundle_id = await bundle(RecordedModrinthAPI(), "iris", replacements={SODIUM: None})
        lock = await service.resolve_fetching(bundle_id)
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


def test_resolve_stores_the_latest_resolution_with_its_digest(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, Any, Any]:
        service, bundle_id = await bundle(RecordedModrinthAPI(), "iris")
        before = await service.resolution(bundle_id)
        lock = await service.resolve_fetching(bundle_id)
        return before, lock, await service.resolution(bundle_id)

    before, lock, after = mongo(scenario)
    assert before is None
    saved, stale = after
    assert saved.lock == lock and stale is False


def test_a_change_to_the_pack_makes_the_resolution_stale(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[bool, bool]:
        service, bundle_id = await bundle(RecordedModrinthAPI(), "iris")
        await service.resolve_fetching(bundle_id)
        await service.pin_set(bundle_id, IRIS, "bAdKrpw8", EDITOR)
        stale = (await service.resolution(bundle_id))[1]  # type: ignore[index]
        await service.resolve_fetching(bundle_id)
        return stale, (await service.resolution(bundle_id))[1]  # type: ignore[index]

    assert mongo(scenario) == (True, False)


def test_a_dependency_of_the_last_resolution_can_be_pinned(mongo: MongoRunner) -> None:
    # "Usar la beta" on a dependency's card: the dependency is not in the pack, but the pack's last
    # resolution names it. A project neither in the pack nor in that resolution is still refused.
    async def scenario() -> tuple[dict[str, str], int]:
        api = RecordedModrinthAPI()
        service, bundle_id = await bundle(api, "iris")
        with pytest.raises(HTTPException) as before:
            await service.pin_set(bundle_id, SODIUM, "v4PSXean", EDITOR)  # nothing resolved yet
        await service.resolve_fetching(bundle_id)
        await service.pin_set(bundle_id, SODIUM, "v4PSXean", EDITOR)
        return (await ModpackDatabase().bundle_get(bundle_id)).pins, before.value.status_code

    assert mongo(scenario) == ({SODIUM: "v4PSXean"}, 422)


def test_a_channel_exception_is_stored_audited_and_reaches_the_request(mongo: MongoRunner) -> None:
    # "Acepta beta para este mod" (owner, 2026-10-09): stored as the admin's choice; whether it has
    # an effect is derived at each resolution (tests/test_resolution.py).
    async def scenario() -> tuple[Any, ...]:
        audit = RecordingAudit()
        service, bundle_id = await bundle(RecordedModrinthAPI(), "iris")
        service.audit = audit
        await service.resolve_fetching(bundle_id)
        await service.channel_exception_set(bundle_id, IRIS, "beta", EDITOR)
        stored = (await ModpackDatabase().bundle_get(bundle_id)).channel_exceptions
        stale = (await service.resolution(bundle_id))[1]  # type: ignore[index]
        await service.channel_exception_unset(bundle_id, IRIS, EDITOR)
        after = (await ModpackDatabase().bundle_get(bundle_id)).channel_exceptions
        return stored, stale, after, [(e[0], e[2]) for e in audit.events]

    stored, stale, after, events = mongo(scenario)
    assert stored == {IRIS: "beta"} and stale is True and after == {}
    assert events == [("bundle.channel_exception", {"project_id": IRIS, "channel": "beta"}),
                      ("bundle.channel_exception.delete", {"project_id": IRIS})]


@pytest.mark.parametrize("project, channel", [("IRIS", "release"), ("IRIS", "nightly"), ("SODIUM", "beta"),
                                              ("not/an.id", "beta")])
def test_a_channel_exception_is_refused_for_a_bad_channel_or_a_project_the_pack_does_not_name(
        mongo: MongoRunner, project: str, channel: str) -> None:
    async def scenario() -> tuple[int, dict[str, str]]:
        service, bundle_id = await bundle(RecordedModrinthAPI(), "iris")
        target = {"IRIS": IRIS, "SODIUM": SODIUM}.get(project, project)
        with pytest.raises(HTTPException) as refused:
            await service.channel_exception_set(bundle_id, target, channel, EDITOR)  # nothing resolved yet
        return refused.value.status_code, (await ModpackDatabase().bundle_get(bundle_id)).channel_exceptions

    assert mongo(scenario) == (422, {})


def test_the_request_carries_the_build_in_force_as_floors(mongo: MongoRunner) -> None:
    # A mod never goes back (owner, 2026-10-12): every project of the recommended build, else the
    # latest, is a floor of the next resolution; a pack without builds has none.
    async def scenario() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
        service, bundle_id = await bundle(RecordedModrinthAPI(), "iris")
        stored = await ModpackDatabase().bundle_get(bundle_id)
        before = (await service._request(stored)).floors
        build = await service.build_create(bundle_id, actor=EDITOR)
        after = (await service._request(await ModpackDatabase().bundle_get(bundle_id))).floors
        return before, after, {e.project_id: e.version_id for e in build.lock.entries}

    before, after, locked = mongo(scenario)
    assert before == {} and after == locked and SODIUM in after
