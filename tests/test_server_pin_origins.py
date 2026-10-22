"""El origen «servidor» de una fijación (decisión del dueño, 2026-10-21; d-7985ee-fce4ff).

Al escanear un servidor vinculado, cada mod FIJADO de su pack cuya versión instalada difiere de la
fijada suma (o actualiza) su origen «servidor», con el nombre del servidor y su validación, nunca en
uso solo: la fijación no cambia, y se escribe solo si nadie la cambió mientras tanto. Un mod sin
fijar no recibe nada. Sin MongoDB: las bases son dobles; la regla pura está en `tests/test_pin_origins.py`.
"""
import asyncio
from datetime import datetime, timezone
from typing import Any, Optional, cast

from beanie import PydanticObjectId

from src.library.scan import ScanFile
from src.model.modpack import ModpackBundle, PinRecord, PinSource
from src.model.pterodactyl import PterodactylServer, ServerScan
from src.model.utils import MinecraftLoaders
from src.services.pterodactyl import PterodactylService

NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
BUNDLE_ID = PydanticObjectId()


def jar(name: str, project_id: Optional[str], version_id: Optional[str]) -> ScanFile:
    return ScanFile(path=f"/mods/{name}.jar", size=1, modified_at=NOW, status="identified" if project_id else "unknown",
                    sha1="s1", sha512="s5", project_id=project_id, version_id=version_id)


class Packs:
    def __init__(self, bundle: ModpackBundle) -> None:
        self.bundle = bundle
        self.writes: list[tuple[str, PinRecord, Optional[str], object]] = []

    async def bundle_get(self, bundle_id: str) -> ModpackBundle:
        return self.bundle

    async def bundle_pin_record_set(self, bundle_id: str, project_id: str, record: PinRecord, pin: Optional[str],
                                    when_pin: object = None) -> bool:
        self.writes.append((project_id, record, pin, when_pin))
        return True


class Catalog:
    async def version_find_by_version_ids(self, version_ids: list[str]) -> list[Any]:
        return []


def offered(bundle: ModpackBundle, files: list[ScanFile]) -> list[tuple[str, PinRecord, Optional[str], object]]:
    packs = Packs(bundle)
    service = PterodactylService(pterodactylAPI=cast(Any, None), PterodactylDB=cast(Any, None),
                                 modrinthAPI=cast(Any, None), modrinthDB=cast(Any, Catalog()), modpackDB=cast(Any, packs),
                                 audit=cast(Any, None), jobDB=cast(Any, object()), clock=lambda: NOW)
    server = PterodactylServer.model_construct(id=PydanticObjectId(), identifier="1a2b3c4d", uuid="u", name="Survival",
                                               node="n1", bundle_id=str(BUNDLE_ID))
    scan = ServerScan.model_construct(id=PydanticObjectId(), server_id=server.id, directory="/mods", scanned_at=NOW,
                                      full=False, files=files)
    asyncio.run(service._server_pin_origins(server, scan))
    return packs.writes


def pack(pins: dict[str, str]) -> ModpackBundle:
    return ModpackBundle.model_construct(id=BUNDLE_ID, name="Pack", version="1.20.1", loader=MinecraftLoaders.forge,
                                         pins=pins)


def test_a_scan_offers_the_servers_version_of_a_pinned_mod_without_putting_it_in_use() -> None:
    writes = offered(pack({"AAAAAAAA": "va-1", "BBBBBBBB": "vb-1"}),
                     [jar("a", "AAAAAAAA", "va-2"), jar("b", "BBBBBBBB", "vb-1"), jar("c", "CCCCCCCC", "vc-5"),
                      jar("x", None, None)])
    [(project_id, record, pin, when_pin)] = writes
    # The pin stays, and only if nobody changed it meanwhile.
    assert (project_id, pin, when_pin) == ("AAAAAAAA", "va-1", "va-1")
    assert record.selected == PinSource.admin
    server = record.origin(PinSource.server)
    assert server is not None and (server.version_id, server.label, server.valid, server.at) == ("va-2", "Survival", None, NOW)


def test_a_pack_without_pins_gets_nothing() -> None:
    assert offered(pack({}), [jar("a", "AAAAAAAA", "va-2")]) == []
