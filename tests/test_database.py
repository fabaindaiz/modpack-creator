"""La capa de base contra un MongoDB real (i-7985ee-ade111), con el fixture `mongo` de conftest."""
from datetime import datetime, timezone
from typing import Any, cast

import pytest

from src.database.modrinth import ModrinthDatabase
from src.database.users import UsersDatabase
from src.model.modrinth import ModrinthProject, ModrinthVersion
from src.database.pterodactyl import PterodactylDatabase
from src.model.pterodactyl import PterodactylServer
from src.services.pterodactyl import PterodactylService
from src.model.users import UsersInternal, UsersProviderAuthentik
from src.services.modrinth import ModrinthService
from tests.conftest import MongoRunner

pytestmark = pytest.mark.network

ISS = "https://idp.example.org/application/o/modpack/"


def identity(sub: str = "abc123", email: str = "owner@example.org", name: str = "Owner") -> UsersProviderAuthentik:
    return UsersProviderAuthentik(iss=ISS, sub=sub, name=name, email=email, email_verified=True)


def test_a_new_identity_creates_one_user_and_finds_it_again(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, Any, int]:
        users = UsersDatabase()
        first = await users.user_from_provider(identity())
        again = await users.user_from_provider(identity(email="changed@example.org"))
        return first.id, again.id, await UsersInternal.count()

    first, again, count = mongo(scenario)
    assert first is not None and first == again
    assert count == 1


def test_the_identity_is_issuer_and_subject_not_email(mongo: MongoRunner) -> None:
    async def scenario() -> int:
        users = UsersDatabase()
        await users.user_from_provider(identity(sub="one"))
        await users.user_from_provider(identity(sub="two"))
        return await UsersInternal.count()

    assert mongo(scenario) == 2


def test_a_user_stored_before_subjects_is_migrated_by_email_once(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, Any, dict[str, Any], int]:
        legacy = await UsersInternal.get_pymongo_collection().insert_one({
            "email": "owner@example.org",
            "name": "Old name",
            "provider": {"type": "authentik", "iss": ISS, "name": "Old name",
                         "email": "owner@example.org", "email_verified": True},
        })
        user = await UsersDatabase().user_from_provider(identity(name="New name"))
        stored = await UsersInternal.get(legacy.inserted_id)
        assert stored is not None
        return legacy.inserted_id, user.id, stored.model_dump(), await UsersInternal.count()

    legacy_id, user_id, stored, count = mongo(scenario)
    assert user_id == legacy_id
    assert stored["provider"]["sub"] == "abc123"
    assert stored["name"] == "New name"
    assert count == 1


def project(**overrides: Any) -> ModrinthProject:
    fields: dict[str, Any] = {
        "id": "AANobbMI", "slug": "sodium", "team": "t", "title": "Sodium", "description": "old",
        "body": "", "client_side": "required", "server_side": "unsupported", "project_type": "mod",
        "status": "approved", "downloads": 1, "followers": 1, "categories": [],
        "additional_categories": [], "versions": [], "game_versions": ["1.21.1"],
        "loaders": ["fabric"], "issues_url": None, "source_url": None, "wiki_url": None,
        "published": datetime(2020, 1, 1, tzinfo=timezone.utc),
        "updated": datetime(2020, 1, 1, tzinfo=timezone.utc),
    }
    fields.update(overrides)
    return ModrinthProject.parse(**fields)


def version(version_id: str, project_id: str = "AANobbMI", **overrides: Any) -> ModrinthVersion:
    fields: dict[str, Any] = {
        "id": version_id, "project_id": project_id, "author_id": "a", "name": version_id,
        "version_number": version_id, "version_type": "release", "status": "listed",
        "downloads": 1, "game_versions": ["1.21.1"], "loaders": ["fabric"], "featured": False,
        "date_published": datetime(2020, 1, 1, tzinfo=timezone.utc), "dependencies": [],
        "files": [{"hashes": {"sha512": version_id * 4, "sha1": version_id}, "url": "u",
                   "filename": f"{version_id}.jar", "primary": True, "size": 1}],
    }
    fields.update(overrides)
    return ModrinthVersion.parse(**fields)


class FakeModrinthAPI:
    """Lo que Modrinth devuelve hoy, en el orden que se le dé: la API no promete orden."""

    def __init__(self, *projects: ModrinthProject, versions: tuple[ModrinthVersion, ...] = ()) -> None:
        self.projects = {p.slug: p for p in projects}
        self.versions = {v.version_id: v for v in versions}

    async def project_get(self, slug: str) -> ModrinthProject:
        return self.projects[slug]

    async def project_get_many(self, slugs: list[str]) -> list[ModrinthProject]:
        return [self.projects[slug] for slug in reversed(slugs)]

    async def version_get_many(self, ids: list[str]) -> list[ModrinthVersion]:
        return [self.versions[i] for i in ids]


def modrinth(api: FakeModrinthAPI) -> ModrinthService:
    return ModrinthService(modrinthAPI=cast(Any, api), modrinthDB=ModrinthDatabase())


def test_project_update_writes_what_modrinth_returns(mongo: MongoRunner) -> None:
    async def scenario() -> str:
        database = ModrinthDatabase()
        stored = await database.project_insert(project(description="old"))
        await modrinth(FakeModrinthAPI(project(description="new"))).project_update(str(stored.id))
        reread = await ModrinthProject.get(stored.id)
        assert reread is not None
        return reread.description

    assert mongo(scenario) == "new"


def test_project_update_many_writes_each_project_whatever_order_modrinth_answers(mongo: MongoRunner) -> None:
    async def scenario() -> dict[str, str]:
        database = ModrinthDatabase()
        a = await database.project_insert(project(id="A", slug="a", description="old"))
        b = await database.project_insert(project(id="B", slug="b", description="old"))
        api = FakeModrinthAPI(project(id="A", slug="a", description="new a"),
                              project(id="B", slug="b", description="new b"))
        await modrinth(api).project_update_many([str(a.id), str(b.id)])
        return {p.slug: p.description async for p in ModrinthProject.find()}

    assert mongo(scenario) == {"a": "new a", "b": "new b"}


def test_creating_projects_already_stored_refreshes_them_and_loses_none(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[dict[str, str], set[str]]:
        await ModrinthDatabase().project_insert(project(id="B", slug="b", description="old"))
        api = FakeModrinthAPI(*(project(id=x.upper(), slug=x, description=f"new {x}") for x in "abc"))
        created = await modrinth(api).project_create_many(["a", "b", "c"])
        stored = {p.slug: p.description async for p in ModrinthProject.find()}
        return stored, {p.slug for p in created}

    stored, created = mongo(scenario)
    assert stored == {"a": "new a", "b": "new b", "c": "new c"}
    assert created == {"a", "b", "c"}


def test_a_project_keeps_many_versions_and_refreshing_them_does_not_duplicate(mongo: MongoRunner) -> None:
    async def scenario() -> dict[str, int]:
        await ModrinthDatabase().project_insert(project(versions=["v1", "v2"]))
        api = FakeModrinthAPI(versions=(version("v1"), version("v2")))
        await modrinth(api).project_version_update_all("sodium")
        api.versions["v2"] = version("v2", downloads=99)
        await modrinth(api).project_version_update_all("sodium")
        return {v.version_id: v.downloads async for v in ModrinthVersion.find()}

    assert mongo(scenario) == {"v1": 1, "v2": 99}


def test_startup_replaces_the_old_unique_index_on_version_project_id(mongo: MongoRunner) -> None:
    """Producción puede tener el índice `unique` viejo: con él, `init_beanie` no arranca."""
    from pymongo import AsyncMongoClient
    from src.model import init_databases

    async def scenario() -> dict[str, Any]:
        collection = ModrinthVersion.get_pymongo_collection()
        client: AsyncMongoClient = collection.database.client
        prefix = collection.database.name.removesuffix("modrinth")
        await collection.drop_indexes()
        await collection.create_index("project_id", unique=True)
        await init_databases(client, prefix)
        await init_databases(client, prefix)
        index: dict[str, Any] = (await ModrinthVersion.get_pymongo_collection().index_information())["project_id_1"]
        return index

    assert "unique" not in mongo(scenario)


class FakePterodactylAPI:
    def __init__(self, server: PterodactylServer) -> None:
        self.server = server

    async def server_get(self, uuid: str) -> PterodactylServer:
        assert uuid == self.server.uuid
        return self.server


def server(**overrides: Any) -> PterodactylServer:
    fields: dict[str, Any] = {
        "identifier": "1a2b3c4d", "internal_id": 1, "uuid": "1a2b3c4d-0000", "name": "Survival",
        "node": "n1", "description": "old", "status": None, "is_suspended": False,
        "is_installing": False, "is_transferring": False,
    }
    fields.update(overrides)
    return PterodactylServer(**fields)


def test_server_update_writes_what_the_panel_returns(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[str, int]:
        database = PterodactylDatabase()
        stored = await database.server_insert(server(description="old"))
        service = PterodactylService(pterodactylAPI=cast(Any, FakePterodactylAPI(server(description="new"))),
                                     PterodactylDB=database, modrinthAPI=cast(Any, None),
                                     modrinthDB=ModrinthDatabase(), modpackDB=cast(Any, None))
        await service.server_update(str(stored.id))
        reread = await PterodactylServer.get(stored.id)
        assert reread is not None
        return reread.description, await PterodactylServer.count()

    assert mongo(scenario) == ("new", 1)


def test_a_user_read_after_a_change_is_the_changed_user(mongo: MongoRunner) -> None:
    """Un permiso revocado tiene que valer en el siguiente request, no en 10 minutos (i-7985ee-c7b593)."""
    async def scenario() -> str:
        users = UsersDatabase()
        user = await users.user_from_provider(identity(name="Before"))
        await users.user_get(str(user.id))
        await UsersInternal.get_pymongo_collection().update_one({"_id": user.id}, {"$set": {"name": "After"}})
        return (await users.user_get(str(user.id))).name

    assert mongo(scenario) == "After"


def test_every_collection_is_named_after_its_class(mongo: MongoRunner) -> None:
    """Las colecciones de producción se llaman como la clase (i-7985ee-c1de10): Beanie 2 ignora
    `Settings.collection`. Un `name` nuevo en un modelo dejaría sus datos huérfanos sin migrar."""
    from beanie import Document
    from src.model.modpack import ModpackBuild, ModpackBundle, ModpackReplacement
    from src.model.pterodactyl import PterodactylInstance, ServerScan

    async def scenario() -> dict[str, str]:
        models: list[type[Document]] = [ModpackBundle, ModpackBuild, ModpackReplacement, ModrinthProject,
                                        ModrinthVersion, PterodactylInstance, PterodactylServer, ServerScan,
                                        UsersInternal]
        return {m.__name__: m.get_collection_name() for m in models}

    names = mongo(scenario)
    assert names == {name: name for name in names}
