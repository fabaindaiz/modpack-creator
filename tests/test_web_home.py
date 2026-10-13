"""El tablero (B2, d-7985ee-c96727): la franja «Necesita atención» arriba y, debajo, servidores,
builds y trabajos como tarjetas. Los servicios son los dobles de `tests/test_web_base.py`."""
import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from beanie import PydanticObjectId

from src.library.resolution import Lock, LockEntry, Problem
from src.model.jobs import Job
from src.model.modpack import ModpackBuild, ModpackBundle
from src.model.modpack.project import ModpackResolution
from src.model.modpack.update_check import AvailableUpdate, UpdateCheck
from src.model.utils import MinecraftLoaders
from src.services.modpack import ModpackService
from tests._actors import EDITOR
from tests.test_web_base import (BUNDLE_ID, T0, FakeChecks, FakeJobs, FakeModpack, FakePterodactyl, client,
                                 default_fakes, server)


def strip(text: str) -> str:
    """The «Necesita atención» section of the page."""
    found = re.search(r'<section[^>]*id="atencion".*?</section>', text, re.S)
    assert found, "no «Necesita atención» section"
    return found.group(0)


def resolution(undecided: int = 0, problems: int = 0) -> ModpackResolution:
    entries = [LockEntry.model_construct(project_id=f"p{n}", server="admin") for n in range(undecided)]
    found = [Problem(kind="unresolvable", project_id=f"q{n}", detail="x") for n in range(problems)]
    lock = Lock(entries=entries, problems=found, suggestions=[], needs_projects=set(), needs_types=set(),
                needs_versions=set())
    return ModpackResolution.model_construct(bundle_id=BUNDLE_ID, resolved_at=T0, request_digest="d", lock=lock)


def calm_fakes() -> tuple[FakePterodactyl, FakeModpack, FakeJobs]:
    """One server in sync with its pack, the pack without pending: nothing to attend."""
    pterodactyl, modpack, jobs = default_fakes()
    linked = pterodactyl.servers[0]
    return FakePterodactyl([linked], {str(linked.id): "✅ coincide"}), modpack, jobs


def test_the_strip_lists_what_needs_attention_with_one_action_each() -> None:
    pterodactyl, modpack, jobs = default_fakes()
    linked, loose = pterodactyl.servers
    section = strip(client(EDITOR, (pterodactyl, modpack, jobs)).get("/").text)
    assert "Necesita atención" in section
    assert "Survival no coincide con su build." in section
    assert f'href="/servers/{linked.id}#actualizar"' in section and "Simular" in section
    assert "Creativo nunca se ha escaneado." in section and f'href="/servers/{loose.id}"' in section
    assert "Creativo no tiene un modpack vinculado." in section and f'href="/servers/{loose.id}#modpack"' in section
    # By severity: the drift before the scan, the scan before the link.
    assert section.index("Simular") < section.index("Escanear") < section.index("Vincular")
    assert "Ver todas" not in section


def test_a_failed_job_of_the_last_day_and_a_pack_to_decide_are_in_the_strip() -> None:
    pterodactyl, _, jobs = default_fakes()
    linked = pterodactyl.servers[0]
    now = datetime.now(timezone.utc)
    failed = Job.model_construct(id=PydanticObjectId(), kind="server_update", key=f"server:{linked.id}",
                                 payload={"server_id": str(linked.id)}, status="failed",
                                 created_at=now - timedelta(hours=2), finished_at=now - timedelta(hours=1))
    old = Job.model_construct(id=PydanticObjectId(), kind="server_scan", key=None, payload={}, status="failed",
                              created_at=now - timedelta(days=3), finished_at=now - timedelta(days=3))
    modpack = FakeModpack(default_fakes()[1].rows, {str(BUNDLE_ID): resolution(undecided=2)})
    section = strip(client(EDITOR, (pterodactyl, modpack, FakeJobs(jobs.jobs, [failed, old]))).get("/").text)
    assert "Falló la actualización de Survival." in section and f'href="/jobs/{failed.id}"' in section
    assert f'href="/jobs/{old.id}"' not in section
    assert "Pack Uno tiene 2 mods por decidir." in section
    assert f'href="/modpacks/{BUNDLE_ID}?vista=decidir"' in section
    assert section.index("Falló") < section.index("Simular")


def test_a_pack_with_updates_asks_to_see_them() -> None:
    check = UpdateCheck.model_construct(bundle_id=BUNDLE_ID, build_number=3, checked_at=T0, error=None, updates=[
        AvailableUpdate(project_id="a", from_version="1", to_version="2"),
        AvailableUpdate(project_id="b", from_version="1", to_version="2", pinned=True)])
    section = strip(client(EDITOR, calm_fakes(), FakeChecks({BUNDLE_ID: check})).get("/").text)
    assert "Pack Uno tiene 1 actualización disponible." in section
    assert f'href="/modpacks/{BUNDLE_ID}/actualizaciones"' in section and "Ver actualizaciones" in section


def test_more_than_five_go_inside_see_all() -> None:
    servers = [server(f"Suelto {n}") for n in range(4)]  # each: never scanned and without a pack
    section = strip(client(EDITOR, (FakePterodactyl(servers, {}), default_fakes()[1], FakeJobs([]))).get("/").text)
    shown, _, hidden = section.partition("<details")
    assert hidden, "no «Ver todas»"
    assert "Ver todas (8)" in hidden
    assert shown.count("Escanear") + shown.count("Vincular") == 5
    assert hidden.count("Escanear") + hidden.count("Vincular") == 3


def test_nothing_to_attend_says_all_is_well_with_the_latest_check() -> None:
    older = UpdateCheck.model_construct(bundle_id=BUNDLE_ID, checked_at=T0 - timedelta(days=1), updates=[])
    newer = UpdateCheck.model_construct(bundle_id=PydanticObjectId(), checked_at=T0, updates=[])
    section = strip(client(EDITOR, calm_fakes(), FakeChecks({BUNDLE_ID: older, cast(Any, newer.bundle_id): newer}))
                    .get("/").text)
    assert "Todo en orden" in section
    assert "Última revisión de Modrinth" in section and T0.isoformat() in section
    assert "Simular" not in section and "<details" not in section


def test_nothing_to_attend_and_never_checked() -> None:
    section = strip(client(EDITOR, calm_fakes()).get("/").text)
    assert "Todo en orden" in section and "Última revisión de Modrinth" not in section


def test_the_dashboard_lists_are_cards_not_tables() -> None:
    fakes = default_fakes()
    text = client(EDITOR, fakes).get("/").text
    assert "<table" not in text
    for caption in ("Servidores", "Últimos builds", "Trabajos"):
        assert f'aria-label="{caption}"' in text
    assert f'href="/modpacks/{BUNDLE_ID}"' in text and "#3" in text  # the latest build
    assert "grid-cols-[minmax(0,1fr)_" in text  # the long text takes the rest, the short ones their width


def test_an_empty_dashboard_says_so_in_each_list() -> None:
    text = client(EDITOR, (FakePterodactyl([], {}), FakeModpack([]), FakeJobs([]))).get("/").text
    assert "No hay servidores registrados." in text and "No hay modpacks." in text
    assert "Todavía no hay trabajos." in text and "Todo en orden" in text


class FakeModpackDB:
    def __init__(self, saved: dict[PydanticObjectId, ModpackResolution]) -> None:
        self.saved = saved
        self.asked: list[PydanticObjectId] = []

    async def resolution_get(self, bundle_id: PydanticObjectId) -> ModpackResolution | None:
        self.asked.append(bundle_id)
        return self.saved.get(bundle_id)


def test_resolution_many_reads_each_saved_resolution_and_skips_the_rest() -> None:
    saved = resolution(undecided=1)
    database = FakeModpackDB({BUNDLE_ID: saved})
    service = ModpackService(modpackDB=cast(Any, database), modrinthDB=cast(Any, None), modrinthAPI=cast(Any, None),
                             loaderAPI=cast(Any, None), audit=cast(Any, None))
    other = str(PydanticObjectId())
    found = asyncio.run(service.resolution_many([str(BUNDLE_ID), other, "not-an-id"]))
    assert found == {str(BUNDLE_ID): saved}
    assert database.asked == [BUNDLE_ID, PydanticObjectId(other)]


def test_bundle_rows_keep_name_and_version() -> None:
    bundle = ModpackBundle.model_construct(id=BUNDLE_ID, name="Pack <b>", description="", version="1.21.1",
                                           loader=MinecraftLoaders.fabric, latest_build=None, recommended_build=None)
    build = ModpackBuild.model_construct(id=PydanticObjectId(), bundle_id=BUNDLE_ID, number=1, created_at=T0)
    text = client(EDITOR, (FakePterodactyl([], {}), FakeModpack([(bundle, build)]), FakeJobs([]))).get("/").text
    assert "Pack &lt;b&gt;" in text and "Pack <b>" not in text
    assert "1.21.1 · fabric" in text
