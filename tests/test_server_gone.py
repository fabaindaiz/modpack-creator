"""Un servidor que se borra mientras «Crear modpack desde este servidor» corre (decisión del dueño,
2026-10-21): si el pack recién creado sigue vacío se borra, y lo que corría termina con «El servidor
se borró mientras se creaba su modpack: no quedó nada creado»; si alguien ya le agregó mods, se deja.
Pasa en la petición (vincular da 404) y en el trabajo de importación que ella encola. Sin MongoDB:
las bases son dobles (los documentos se arman con `model_construct`, sin Beanie inicializado).
"""
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional, cast

import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException

from src.exceptions import HTTP_404_NOT_FOUND
from src.model.jobs import Job
from src.model.modpack import ModpackBundle
from src.model.pterodactyl import PterodactylServer
from src.model.utils import MinecraftLoaders
from src.services.jobs import JobFailed
from src.services.pterodactyl import SERVER_GONE, PterodactylService
from tests._actors import OPERATOR, RecordingAudit

SERVER_ID = str(PydanticObjectId())


@pytest.fixture(autouse=True)
def documents_without_beanie(monkeypatch: pytest.MonkeyPatch) -> None:
    """The service builds the new pack itself: built here without a collection."""
    from src.services import pterodactyl as module
    monkeypatch.setattr(module, "ModpackBundle", ModpackBundle.model_construct)

BUNDLE_ID = PydanticObjectId()


class Servers:
    """The servers' database: the server exists until `gone`."""

    def __init__(self) -> None:
        self.gone = False
        self.deleted_on_link = False

    async def server_get(self, server_id: str) -> PterodactylServer:
        if self.gone:
            raise HTTP_404_NOT_FOUND(f"Server '{server_id}' not found")
        return PterodactylServer.model_construct(id=PydanticObjectId(server_id), identifier="1a2b3c4d", uuid="u",
                                                 name="Survival", node="n1", bundle_id=None)

    async def server_link_if_unlinked(self, server_id: str, bundle_id: str) -> bool:
        if self.deleted_on_link:
            self.gone = True
        return bool(await self.server_get(server_id))


class Packs:
    """The packs' database: `empty` says whether the new pack is still empty when it is deleted."""

    def __init__(self, empty: bool = True) -> None:
        self.empty = empty
        self.deleted: list[str] = []

    async def bundle_insert(self, bundle: ModpackBundle) -> ModpackBundle:
        return ModpackBundle.model_construct(**{**dict(bundle), "id": BUNDLE_ID})

    async def bundle_delete_empty(self, bundle_id: str) -> bool:
        if self.empty:
            self.deleted.append(bundle_id)
        return self.empty


def service(servers: Servers, packs: Packs, audit: RecordingAudit) -> PterodactylService:
    return PterodactylService(pterodactylAPI=cast(Any, None), PterodactylDB=cast(Any, servers),
                              modrinthAPI=cast(Any, None), modrinthDB=cast(Any, None), modpackDB=cast(Any, packs),
                              audit=audit, jobDB=cast(Any, object()))


def test_a_server_deleted_before_the_link_deletes_the_empty_pack_and_says_so() -> None:
    servers, packs, audit = Servers(), Packs(), RecordingAudit()
    servers.deleted_on_link = True
    with pytest.raises(HTTPException) as caught:
        import asyncio
        asyncio.run(service(servers, packs, audit).bundle_for_server(
            SERVER_ID, "Creativo", "26.3", MinecraftLoaders.fabric, False, OPERATOR))
    assert caught.value.status_code == 404
    assert caught.value.detail == "El servidor se borró mientras se creaba su modpack: no quedó nada creado"
    assert packs.deleted == [str(BUNDLE_ID)]
    assert [e[0] for e in audit.events] == ["bundle.create", "bundle.delete"]
    assert audit.events[-1][2] == {"reason": "server deleted meanwhile"}


def test_a_pack_that_is_no_longer_empty_is_left() -> None:
    servers, packs, audit = Servers(), Packs(empty=False), RecordingAudit()
    servers.deleted_on_link = True
    with pytest.raises(HTTPException) as caught:
        import asyncio
        asyncio.run(service(servers, packs, audit).bundle_for_server(
            SERVER_ID, "Creativo", "26.3", MinecraftLoaders.fabric, False, OPERATOR))
    assert caught.value.status_code == 404 and caught.value.detail != SERVER_GONE
    assert [e[0] for e in audit.events] == ["bundle.create"]


class Context:
    """A job's context that runs each step."""

    async def step(self, name: str, action: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
        return await action()

    async def progress(self, label: str, done: int, total: int) -> None:
        return None


def import_job(created: Optional[str]) -> Job:
    payload: dict[str, Any] = {"server_id": SERVER_ID}
    if created is not None:
        payload["created_bundle_id"] = created
    return Job.model_construct(kind="server_import", payload=payload, checkpoint={},
                               requested_by=OPERATOR.ref(), created_at=datetime.now(timezone.utc))


def run_import(servers: Servers, packs: Packs, audit: RecordingAudit, created: Optional[str]) -> None:
    import asyncio
    asyncio.run(service(servers, packs, audit)._import_run(import_job(created), cast(Any, Context())))


def test_the_import_job_of_a_pack_made_for_a_server_now_gone_deletes_it_if_empty() -> None:
    servers, packs, audit = Servers(), Packs(), RecordingAudit()
    servers.gone = True
    with pytest.raises(JobFailed) as caught:
        run_import(servers, packs, audit, str(BUNDLE_ID))
    assert str(caught.value) == "El servidor se borró mientras se creaba su modpack: no quedó nada creado"
    assert packs.deleted == [str(BUNDLE_ID)]
    assert [e[0] for e in audit.events] == ["bundle.delete"]


def test_the_import_job_leaves_a_pack_with_mods_and_a_plain_import_alone() -> None:
    servers, packs, audit = Servers(), Packs(empty=False), RecordingAudit()
    servers.gone = True
    with pytest.raises(HTTPException):
        run_import(servers, packs, audit, str(BUNDLE_ID))
    # «Importar» on a linked server never deletes its pack, empty or not.
    packs = Packs()
    with pytest.raises(HTTPException):
        run_import(servers, packs, audit, None)
    assert packs.deleted == [] and audit.events == []
