"""Por qué un escaneo fue completo (decisión del dueño, 2026-10-21) y el cambio de nodo.

Un escaneo completo guarda su motivo (`full_reason`): el primero, uno pedido, el periódico, una
restauración en la actividad, un escaneo anterior más viejo que la actividad, la actividad ilegible
(sin `activity.read`, o por otra falla), un cambio de nodo o el nodo ilegible. Una transferencia
entre nodos no deja evento en la actividad y conserva las fechas: el escaneo guarda el `node` del
servidor (`GET /api/client/servers/{id}`, docs/research/2026-10-12-actividad-pterodactyl.md §4) y va
completo cuando cambia. La ficha del servidor muestra el motivo junto al último escaneo cuando hay un
texto llano para él. Sin MongoDB: las bases son dobles y los documentos se arman con `model_construct`.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, cast

import pytest
from beanie import PydanticObjectId

from src.exceptions import UpstreamError
from src.library.scan import FULL_REASON_TEXT, full_reason_text
from src.model.pterodactyl import PterodactylFile, PterodactylServer, ServerScan
from src.services import pterodactyl as module
from src.services.pterodactyl import PterodactylService

NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
SERVER_ID = str(PydanticObjectId())


@pytest.fixture(autouse=True)
def documents_without_beanie(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "ServerScan", ServerScan.model_construct)


def server(node: str) -> PterodactylServer:
    return PterodactylServer.model_construct(id=PydanticObjectId(SERVER_ID), identifier="1a2b3c4d", uuid="1a2b3c4d-0000",
                                             name="Survival", node=node, bundle_id=None)


class Panel:
    """The panel: one jar, the server on `node`, and its activity (or the error reading it)."""

    def __init__(self, node: str = "n1") -> None:
        self.node = node
        self.node_error: Optional[Exception] = None
        self.activity: list[tuple[str, datetime]] = []
        self.activity_error: Optional[Exception] = None
        self.calls: list[str] = []

    async def server_get(self, server_id: str) -> PterodactylServer:
        self.calls.append("server")
        if self.node_error is not None:
            raise self.node_error
        return server(self.node)

    async def server_activity(self, server_id: str, events: list[str]) -> list[tuple[str, datetime]]:
        self.calls.append("activity")
        if self.activity_error is not None:
            raise self.activity_error
        return list(self.activity)

    async def server_files(self, server_id: str, directory: str) -> list[PterodactylFile]:
        return [PterodactylFile(name="a.jar", size=1, mode_bits="644", is_file=True, is_symlink=False,
                                mimetype="application/jar", created_at=T0, modified_at=T0)]

    async def server_files_download(self, server_id: str, filepath: str) -> bytes:
        self.calls.append("download")
        return b"a"


class Servers:
    """The servers' database: the server as stored (the node it had when added) and its scans."""

    def __init__(self) -> None:
        self.scans: list[ServerScan] = []

    async def server_get(self, server_id: str) -> PterodactylServer:
        return server("n1")

    async def scan_latest(self, server_id: str, directory: str) -> Optional[ServerScan]:
        return self.scans[-1] if self.scans else None

    async def scan_insert(self, scan: ServerScan) -> ServerScan:
        self.scans.append(scan)
        return scan


class Modrinth:
    async def files_hash_post_many(self, hashes: list[str], algorithm: str = "sha512") -> dict[str, Any]:
        return {}


class Audit:
    async def record(self, *args: Any, **kwargs: Any) -> None:
        return None


def scans(panel: Panel, servers: Servers, *asks: dict[str, Any], clock: datetime = NOW) -> list[ServerScan]:
    service = PterodactylService(pterodactylAPI=cast(Any, panel), PterodactylDB=cast(Any, servers),
                                 modrinthAPI=cast(Any, Modrinth()), modrinthDB=cast(Any, None),
                                 modpackDB=cast(Any, None), audit=cast(Any, Audit()), jobDB=cast(Any, object()),
                                 clock=lambda: clock)

    async def run() -> list[ServerScan]:
        return [await service.scan(SERVER_ID, actor=None, **ask) for ask in asks]
    return asyncio.run(run())


def test_the_first_scan_is_full_because_it_is_the_first_and_stores_the_node() -> None:
    [first] = scans(Panel(node="n7"), Servers(), {})
    assert (first.full, first.full_reason, first.node) == (True, "first", "n7")


def test_a_quiet_second_scan_is_not_full_and_has_no_reason() -> None:
    panel, servers = Panel(), Servers()
    _, second = scans(panel, servers, {}, {})
    assert (second.full, second.full_reason, second.node) == (False, None, "n1")


def test_a_node_change_makes_the_scan_full_without_asking_the_activity() -> None:
    panel, servers = Panel(), Servers()
    scans(panel, servers, {})
    panel.node, panel.calls = "n2", []
    [moved] = scans(panel, servers, {})
    assert (moved.full, moved.full_reason, moved.node) == (True, "node_changed", "n2")
    assert "activity" not in panel.calls and "download" in panel.calls


def test_a_scan_stored_before_the_node_was_kept_does_not_count_as_a_change() -> None:
    panel, servers = Panel(), Servers()
    scans(panel, servers, {})
    servers.scans[-1] = servers.scans[-1].model_copy(update={"node": None})
    [later] = scans(panel, servers, {})
    assert (later.full, later.full_reason, later.node) == (False, None, "n1")


@pytest.mark.parametrize("status, reason", [(403, "no_activity_permission"), (404, "activity_unreadable"),
                                            (500, "activity_unreadable")])
def test_an_unreadable_activity_says_why(status: int, reason: str) -> None:
    panel, servers = Panel(), Servers()
    scans(panel, servers, {})
    panel.activity_error = UpstreamError("Pterodactyl", status, "x")
    [later] = scans(panel, servers, {})
    assert (later.full, later.full_reason) == (True, reason)


def test_a_restore_and_a_scan_older_than_the_activity_say_so() -> None:
    panel, servers = Panel(), Servers()
    scans(panel, servers, {}, clock=NOW - timedelta(hours=1))
    panel.activity = [("server:backup.restore", NOW - timedelta(minutes=30))]
    [restored] = scans(panel, servers, {})
    assert (restored.full, restored.full_reason) == (True, "restored")
    [old] = scans(panel, servers, {}, clock=NOW + timedelta(days=91))
    assert (old.full, old.full_reason) == (True, "too_old")


def test_a_full_scan_asked_for_says_who_asked() -> None:
    panel, servers = Panel(), Servers()
    _, forced, periodic = scans(panel, servers, {}, {"full": True}, {"full": True, "full_reason": "periodic"})
    assert (forced.full, forced.full_reason) == (True, "forced")
    assert (periodic.full, periodic.full_reason) == (True, "periodic")


def test_an_unreadable_node_makes_the_scan_full_and_a_busy_panel_raises() -> None:
    panel, servers = Panel(), Servers()
    scans(panel, servers, {})
    panel.node_error = UpstreamError("Pterodactyl", 500, "x")
    [later] = scans(panel, servers, {})
    assert (later.full, later.full_reason, later.node) == (True, "node_unreadable", None)
    panel.node_error = UpstreamError("Pterodactyl", 409, "busy")
    with pytest.raises(UpstreamError):
        scans(panel, servers, {})


def test_the_panel_words_only_the_reasons_it_can_say_plainly() -> None:
    assert full_reason_text("no_activity_permission") == (
        "Escaneo completo: la key no tiene permiso para leer la actividad (activity.read); con él, los siguientes "
        "solo leen lo que cambió")
    assert full_reason_text(None) is None and full_reason_text("unknown-code") is None
    # Owner, 2026-10-22: the three left with only «completo» get a text too.
    assert full_reason_text("forced") == "Escaneo completo: lo pediste así"
    assert full_reason_text("periodic") == "Escaneo completo: el repaso periódico que relee todo cada tanto"
    assert full_reason_text("node_unreadable") == "Escaneo completo: no se pudo leer en qué nodo está el servidor"
    assert set(FULL_REASON_TEXT) == {"no_activity_permission", "first", "restored", "too_old", "node_changed",
                                     "activity_unreadable", "forced", "periodic", "node_unreadable"}
