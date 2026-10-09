"""Los trabajos de un pack se ven mientras corren (spec 2026-10-09-economia-de-requests, E7–E8):
la franja del pack dice qué se procesa, cuánto va, si espera turno y hace cuánto cambió; la barra
superior, los trabajos abiertos del usuario; el botón manual no duplica el que corre."""
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest
from beanie import PydanticObjectId
from dependency_injector import providers

from src.exceptions import CatalogPending, QuotaExceeded
from src.model.jobs import Job, JobProgress
from src.model.users import Actor
from tests._actors import EDITOR
from tests.test_web_modpacks import BUNDLE_ID, HTMX, setup_all, visible

def job(kind: str = "catalog_refresh", status: str = "running", progress: Optional[JobProgress] = None,
        waiting_until: Optional[datetime] = None, payload: Optional[dict[str, Any]] = None) -> Job:
    return Job.model_construct(
        id=PydanticObjectId(), kind=kind, key=f"catalog:{BUNDLE_ID}", status=status, active=status in ("queued", "running"),
        payload=payload if payload is not None else {"bundle_id": str(BUNDLE_ID)}, step=None, progress=progress,
        waiting_until=waiting_until, updated_at=datetime.now(timezone.utc) - timedelta(seconds=3),
        created_at=datetime.now(timezone.utc) - timedelta(seconds=40),
        attempts=1, max_attempts=3, error=None, requested_by=EDITOR.ref(), checkpoint={})


class FakeJobs:
    def __init__(self) -> None:
        self.mine: list[Job] = []
        self.asked: list[Actor] = []

    async def active_for_user(self, actor: Actor) -> list[Job]:
        self.asked.append(actor)
        return self.mine

    async def recent(self, limit: int = 10) -> list[Job]:
        return []


@pytest.fixture(autouse=True)
def reset_overrides() -> Any:
    yield
    import main

    for provider in (main.container.modpackService, main.container.modrinthService, main.container.updateCheckService,
                     main.container.pterodactylService, main.container.jobService):
        provider.reset_override()


def setup() -> tuple[Any, Any, FakeJobs]:
    import main

    client, modpack, _, _, _ = setup_all(EDITOR)
    jobs = FakeJobs()
    main.container.jobService.override(providers.Object(jobs))
    return client, modpack, jobs


def test_a_resolve_that_needs_modrinth_says_a_job_prepares_it_and_shows_it() -> None:
    # Not an error (the visual sweep flagged the 409 in the console): the work is queued.
    client, modpack, _ = setup()
    modpack.error = CatalogPending(job())
    response = client.post(f"/modpacks/{BUNDLE_ID}/resolve", headers=HTMX)
    assert response.status_code == 200 and response.headers.get("HX-Trigger") == "job-started"
    assert response.headers.get("HX-Reswap") == "none"
    assert "Preparando el catálogo de Modrinth para este pack: se resuelve solo al terminar." in response.text
    assert 'id="job-banner" hx-swap-oob="innerHTML"' in response.text and "Catálogo de Modrinth" in response.text


def test_the_banner_says_what_is_processed_how_far_and_when_it_last_moved() -> None:
    client, modpack, _ = setup()
    modpack.jobs = [job(progress=JobProgress(label="Versiones de los mods", done=80, total=150))]
    response = client.get(f"/modpacks/{BUNDLE_ID}/trabajo", headers=HTMX)
    shown = " ".join(visible(response.text).split())
    assert "Catálogo de Modrinth" in shown and "Versiones de los mods: 80 de 150" in shown
    assert '<progress value="80" max="150"' in response.text
    assert f"pedido por {EDITOR.name}" in shown
    assert "hace 3 s" in shown
    assert 'hx-trigger="every 2s"' in response.text and f'hx-get="/modpacks/{BUNDLE_ID}/trabajo?activo=1"' in response.text


def test_a_job_waiting_its_turn_says_for_how_long_and_why() -> None:
    client, modpack, _ = setup()
    modpack.jobs = [job(waiting_until=datetime.now(timezone.utc) + timedelta(seconds=12))]
    shown = " ".join(visible(client.get(f"/modpacks/{BUNDLE_ID}/trabajo", headers=HTMX).text).split())
    assert "Esperando turno:" in shown and "(límite de Modrinth)" in shown


def test_a_queued_job_says_it_waits_in_the_queue() -> None:
    client, modpack, _ = setup()
    modpack.jobs = [job(status="queued")]
    shown = " ".join(visible(client.get(f"/modpacks/{BUNDLE_ID}/trabajo", headers=HTMX).text).split())
    assert "En cola" in shown


def test_when_the_last_job_ends_the_banner_empties_and_the_pack_reloads() -> None:
    client, modpack, _ = setup()
    modpack.jobs = []
    ended = client.get(f"/modpacks/{BUNDLE_ID}/trabajo?activo=1", headers=HTMX)
    idle = client.get(f"/modpacks/{BUNDLE_ID}/trabajo", headers=HTMX)
    assert ended.headers.get("HX-Trigger") == "job-finished" and "every 2s" not in ended.text
    assert "HX-Trigger" not in idle.headers and visible(idle.text).strip() == ""


def test_the_manual_refresh_announces_its_job_and_its_limit_reads_in_words() -> None:
    client, modpack, _ = setup()
    response = client.post(f"/modpacks/{BUNDLE_ID}/catalogo", headers=HTMX)
    assert response.status_code == 200 and response.headers.get("HX-Trigger") == "job-started"
    assert modpack.calls[-1][0] == "catalog_refresh" and modpack.calls[-1][-1] == EDITOR
    modpack.error = QuotaExceeded("Se puede volver a actualizar en 30 s", 30.0)
    refused = client.post(f"/modpacks/{BUNDLE_ID}/catalogo", headers=HTMX)
    assert "Se puede volver a actualizar en 30 s" in refused.text


def test_the_pack_page_has_the_banner_the_refresh_button_and_reloads_on_job_events() -> None:
    client, modpack, _ = setup()
    modpack.bundle.modrinth_checked_at = datetime.now(timezone.utc) - timedelta(minutes=3)
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert f'hx-get="/modpacks/{BUNDLE_ID}/trabajo"' in text and "job-started from:body" in text
    assert f'hx-post="/modpacks/{BUNDLE_ID}/catalogo"' in text and "Actualizar desde Modrinth" in text
    assert "Revisado en Modrinth hace 3 min" in " ".join(visible(text).split())
    assert 'hx-trigger="job-finished from:body, job-started from:body"' in text


def test_while_a_job_runs_the_resolve_and_refresh_buttons_say_it() -> None:
    client, modpack, _ = setup()
    modpack.jobs = [job()]
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    resolve = text[text.rindex("<button", 0, text.index("/resolve")):]
    assert "disabled" in resolve[:resolve.index(">")] and "En curso…" in text


def test_the_bar_shows_the_users_open_jobs_with_their_progress_and_a_shortcut() -> None:
    client, _, jobs = setup()
    jobs.mine = [job(progress=JobProgress(label="Versiones de los mods", done=8, total=15)),
                 job(kind="server_update", payload={"server_id": "SRV1"})]
    response = client.get("/trabajos/mios", headers=HTMX)
    shown = " ".join(visible(response.text).split())
    assert "2 en curso" in shown and "Versiones de los mods: 8 de 15" in shown
    assert f'href="/modpacks/{BUNDLE_ID}"' in response.text and 'href="/servers/SRV1"' in response.text
    assert 'hx-trigger="every 3s"' in response.text and jobs.asked == [EDITOR]


def test_the_bar_is_empty_and_quiet_without_open_jobs() -> None:
    client, _, jobs = setup()
    response = client.get("/trabajos/mios", headers=HTMX)
    assert visible(response.text).strip() == "" and "every" not in response.text


def test_every_page_loads_the_bar_and_reloads_it_when_a_job_starts() -> None:
    client, _, _ = setup()
    text = client.get("/modpacks").text
    assert 'id="my-jobs"' in text and 'hx-get="/trabajos/mios"' in text and "job-started from:body" in text



def test_the_build_click_brings_the_banner_with_its_job_so_a_fast_job_is_not_missed() -> None:
    client, modpack, _ = setup()
    modpack.queued = job(kind="build_create", status="queued")
    response = client.post(f"/modpacks/{BUNDLE_ID}/builds", headers=HTMX)
    assert 'id="job-banner" hx-swap-oob="innerHTML"' in response.text
    assert f'hx-get="/modpacks/{BUNDLE_ID}/trabajo?activo=1"' in response.text and "Build" in response.text


def test_a_job_of_the_pack_that_failed_says_so_with_its_reason() -> None:
    # Code review (2026-10-09): a build or catalog job that failed ended with no notice at all.
    client, modpack, _ = setup()
    modpack.jobs = []
    failed = job(kind="build_create", status="failed")
    failed.error = "Modrinth respondió con un error (502)"
    modpack.failed_job = failed
    response = client.get(f"/modpacks/{BUNDLE_ID}/trabajo?activo=1", headers=HTMX)
    shown = " ".join(visible(response.text).split())
    assert "Build falló: Modrinth respondió con un error (502)" in shown and "every 2s" not in response.text
    assert response.headers["HX-Trigger"] == "job-finished"


def test_v1_resolve_that_needs_modrinth_answers_202_with_the_job() -> None:
    # Code review (2026-10-09): /v1 resolve answered a bare 409 and the job asked for nobody.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.dependences import get_current_actor
    from src.routers.v1 import include_routers_v1

    _, modpack, _ = setup()
    pending = job()
    modpack.error = CatalogPending(pending)
    app = FastAPI()
    include_routers_v1(app)
    app.dependency_overrides[get_current_actor] = lambda: EDITOR
    response = TestClient(app, base_url="https://testserver").post(f"/v1/modpack/{BUNDLE_ID}/resolve")
    assert response.status_code == 202 and response.json()["_id"] == str(pending.id)  # as /v1 update, rollback, scan
    assert response.json()["kind"] == "catalog_refresh"
    assert modpack.calls[-1] == ("resolve", str(BUNDLE_ID), EDITOR)


def test_a_v1_route_of_a_user_counts_against_their_modrinth_quota() -> None:
    import asyncio

    from src.dependences import get_current_user
    from src.library.api.limiter import CURRENT_USER
    from tests._actors import session_for

    class Request:
        session = session_for(EDITOR)

    async def scenario() -> Optional[str]:
        CURRENT_USER.set(None)
        try:
            await get_current_user(Request(), users=None)  # type: ignore[arg-type]
        except Exception:
            pass
        return CURRENT_USER.get()

    assert asyncio.run(scenario()) == f"{EDITOR.iss}|{EDITOR.sub}"
