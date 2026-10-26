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
from tests._actors import EDITOR, OPERATOR
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
    section = strip(client(OPERATOR, (pterodactyl, modpack, jobs)).get("/").text)
    assert "Necesita atención" in section
    assert "Survival no coincide con su build." in section
    assert f'href="/servers/{linked.id}#actualizar"' in section and "Simular" in section
    assert "Creativo nunca se ha escaneado." in section and f'href="/servers/{loose.id}#escaneo"' in section
    assert "Creativo no tiene un modpack vinculado." in section and f'href="/servers/{loose.id}#modpack"' in section
    # By severity: the drift before the scan, the scan before the link.
    assert section.index("Simular") < section.index("Escanear") < section.index("Vincular")
    assert "Ver todas" not in section


def test_a_failed_job_of_the_last_day_and_a_pack_to_decide_are_in_the_strip() -> None:
    pterodactyl, _, jobs = default_fakes()
    linked = pterodactyl.servers[0]
    now = datetime.now(timezone.utc)
    failed = Job.model_construct(id=PydanticObjectId(), kind="server_update", key=f"server:{linked.id}",
                                 payload={"server_id": str(linked.id), "dry_run": False}, status="failed",
                                 created_at=now - timedelta(hours=2), finished_at=now - timedelta(hours=1))
    old =Job.model_construct(id=PydanticObjectId(), kind="server_scan", key=None, payload={}, status="failed",
                              created_at=now - timedelta(days=3), finished_at=now - timedelta(days=3))
    modpack = FakeModpack(default_fakes()[1].rows, {str(BUNDLE_ID): resolution(undecided=2)})
    section = strip(client(OPERATOR, (pterodactyl, modpack, FakeJobs(jobs.jobs, [failed, old]))).get("/").text)
    assert "Falló la actualización de Survival." in section and f'href="/jobs/{failed.id}"' in section
    assert f'href="/jobs/{old.id}"' not in section
    assert "Pack Uno tiene 2 mods por decidir." in section
    assert f'href="/modpacks/{BUNDLE_ID}?vista=decidir"' in section
    assert section.index("Falló") < section.index("Simular")


def test_a_failed_simulation_reads_as_one_that_left_the_server_as_it_was() -> None:
    """Group 2 (owner, 2026-10-21): the dashboard tells a dry run from a real update by its payload."""
    pterodactyl, modpack, jobs = calm_fakes()
    linked = pterodactyl.servers[0]
    now = datetime.now(timezone.utc)
    failed = Job.model_construct(id=PydanticObjectId(), kind="server_update", key=f"server:{linked.id}",
                                 payload={"server_id": str(linked.id), "dry_run": True}, status="failed",
                                 created_at=now - timedelta(hours=2), finished_at=now - timedelta(hours=1))
    section = strip(client(OPERATOR, (pterodactyl, modpack, FakeJobs(jobs.jobs, [failed]))).get("/").text)
    assert "Falló la simulación de Survival: el servidor no cambió." in section
    assert "Falló la actualización" not in section


def test_a_server_whose_pack_was_deleted_asks_to_link_another() -> None:
    """Group 2 (owner, 2026-10-21): the server keeps the id of a pack that no longer exists."""
    pterodactyl, modpack, jobs = calm_fakes()
    orphan = server("Huérfano", bundle_id=str(PydanticObjectId()))
    loose = server("Creativo")
    drifts = pterodactyl.drifts | {str(orphan.id): "✅ coincide", str(loose.id): "✅ coincide"}
    fakes = (FakePterodactyl([*pterodactyl.servers, orphan, loose], drifts), modpack, jobs)
    section = strip(client(OPERATOR, fakes).get("/").text)
    assert "El modpack de Huérfano se borró: vincula otro." in section
    assert f'href="/servers/{orphan.id}#modpack"' in section
    assert "Huérfano no tiene un modpack vinculado." not in section
    assert "Creativo no tiene un modpack vinculado." in section


def test_a_pack_with_updates_asks_to_see_them() -> None:
    check = UpdateCheck.model_construct(bundle_id=BUNDLE_ID, build_number=3, checked_at=T0, error=None, updates=[
        AvailableUpdate(project_id="a", from_version="1", to_version="2"),
        AvailableUpdate(project_id="b", from_version="1", to_version="2", pinned=True)])
    section = strip(client(EDITOR, calm_fakes(), FakeChecks({BUNDLE_ID: check})).get("/").text)
    assert "Pack Uno tiene 1 actualización disponible." in section
    assert f'href="/modpacks/{BUNDLE_ID}/actualizaciones"' in section and "Ver actualizaciones" in section


def test_more_than_five_go_inside_see_all() -> None:
    servers = [server(f"Suelto {n}") for n in range(4)]  # each: never scanned and without a pack
    section = strip(client(OPERATOR, (FakePterodactyl(servers, {}), default_fakes()[1], FakeJobs([]))).get("/").text)
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
    assert "No hay servidores." in text and "No hay modpacks." in text
    assert "Todavía no hay trabajos." in text and "Todo en orden" in text


def test_bundle_rows_keep_name_and_version() -> None:
    bundle = ModpackBundle.model_construct(id=BUNDLE_ID, name="Pack <b>", description="", version="1.21.1",
                                           loader=MinecraftLoaders.fabric, latest_build=None, recommended_build=None)
    build = ModpackBuild.model_construct(id=PydanticObjectId(), bundle_id=BUNDLE_ID, number=1, created_at=T0)
    text = client(EDITOR, (FakePterodactyl([], {}), FakeModpack([(bundle, build)]), FakeJobs([]))).get("/").text
    assert "Pack &lt;b&gt;" in text and "Pack <b>" not in text
    assert "1.21.1 · fabric" in text


def test_a_failure_a_later_success_fixed_is_not_in_the_strip() -> None:
    """Final review of batch B: the dashboard reads the recent successes too."""
    pterodactyl, _, jobs = default_fakes()
    linked = pterodactyl.servers[0]
    now = datetime.now(timezone.utc)
    failed = Job.model_construct(id=PydanticObjectId(), kind="server_scan", key=f"server:{linked.id}",
                                 payload={"server_id": str(linked.id)}, status="failed",
                                 created_at=now - timedelta(hours=2), finished_at=now - timedelta(hours=2))
    fixed = Job.model_construct(id=PydanticObjectId(), kind="server_scan", key=f"server:{linked.id}",
                                payload={"server_id": str(linked.id)}, status="done",
                                created_at=now - timedelta(hours=1), finished_at=now - timedelta(hours=1))
    text = client(EDITOR, (pterodactyl, default_fakes()[1], FakeJobs(jobs.jobs, [failed], [fixed]))).get("/").text
    assert f'href="/jobs/{failed.id}"' not in strip(text)


def test_a_pack_whose_bridged_mods_have_a_native_version_says_so() -> None:
    """Batch C (C7): counted from the pack's saved resolution, `native_available`."""
    pterodactyl, _, jobs = calm_fakes()
    saved = resolution()
    saved.lock.native_available = {"m1": "v1", "m2": "v2"}
    modpack = FakeModpack(default_fakes()[1].rows, {str(BUNDLE_ID): saved})
    section = strip(client(EDITOR, (pterodactyl, modpack, jobs)).get("/").text)
    assert "Pack Uno: 2 mods ya tienen versión nativa." in section
    assert f'href="/modpacks/{BUNDLE_ID}?vista=puente"' in section


class Waiting(FakePterodactyl):
    """Each server's scan answers only once every server's scan was asked for: read one after another,
    the first never answers."""
    async def scan_latest(self, server_id: str, directory: str = "/mods") -> Any:
        self.asked = getattr(self, "asked", 0) + 1
        for _ in range(50):
            if self.asked >= len(self.servers):
                return await super().scan_latest(server_id, directory)
            await asyncio.sleep(0.001)
        raise AssertionError("the scans were read one after another")


def test_the_dashboard_reads_its_servers_scans_together() -> None:
    pterodactyl, modpack, jobs = default_fakes()
    waiting = Waiting(pterodactyl.servers, pterodactyl.drifts)
    assert client(OPERATOR, (waiting, modpack, jobs)).get("/").status_code == 200


def test_the_first_time_the_empty_lists_say_what_to_do() -> None:
    """O9 D11 (owner, 2026-10-26): «No hay modpacks» and «No hay servidores» led nowhere."""
    import re as _re

    _, _, jobs = calm_fakes()
    text = client(EDITOR, (FakePterodactyl([], {}), FakeModpack([]), jobs)).get("/").text

    def visible(html: str) -> str:
        return " ".join(_re.sub(r"<[^>]+>", " ", html).split())

    servers = text[text.index('id="servidores"'):text.index('id="trabajos"')]
    builds = text[text.index('id="builds"'):]
    assert "No hay servidores." in visible(servers) and 'href="/servers"' in servers and "Traer del panel" in visible(servers)
    assert "No hay modpacks." in visible(builds) and 'href="/modpacks#nuevo"' in builds and 'href="/modpacks/importar"' in builds
