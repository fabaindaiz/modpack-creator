"""Jobs and audit pages of the panel (stage 4): reads only, with doubles for the services."""
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Optional

import pytest
from beanie import PydanticObjectId
from dependency_injector import providers
from fastapi.testclient import TestClient

from src.exceptions import HTTP_404_NOT_FOUND
from src.model.audit import AuditEvent, AuditPage, AuditTarget, Facet
from src.model.jobs import Job, RollbackState
from src.model.users import Actor
from src.model.users.actor import ActorRef
from tests._actors import EDITOR, OPERATOR, session_for
from tests._session import session_cookie
from tests.test_web_modpacks import visible
from tests.test_web_servers import BUNDLE_ID, FakeModpack, FakeModrinth, FakePterodactyl, PENDING_ROLLBACK, SERVER_ID, failed_real_update, job as make_job

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
PLAN = {"preflight": {"build": 3, "bundle": "Pack Uno", "bundle_id": str(BUNDLE_ID), "install": [{"filename": "a.jar"}],
                      "remove": []}}


def finished_dry_run() -> Job:
    return make_job("server_update", status="done", active=False,
                    payload={"server_id": str(SERVER_ID), "dry_run": True}, checkpoint=PLAN,
                    requested_by=ActorRef(iss="i", sub="s", name="Ana"))


def event(name: str = "Ana", action: str = "server.scan", details: Optional[dict[str, Any]] = None,
          target: Optional[AuditTarget] = None) -> AuditEvent:
    return AuditEvent.model_construct(
        id=PydanticObjectId(), at=T0, actor=ActorRef(iss="i", sub="s", name=name), action=action,
        target=target or AuditTarget(kind="server", id="t1"), details=details or {}, request_id="r1")


class FakeJobs:
    def __init__(self) -> None:
        self.jobs: list[Job] = []
        self.calls: list[dict[str, Any]] = []
        self.state: Optional[RollbackState] = None
        self.asked_state: list[str] = []

    async def rollback_state(self, server_id: str) -> Optional[RollbackState]:
        self.asked_state.append(server_id)
        return self.state

    async def find(self, **kwargs: Any) -> list[Job]:
        self.calls.append(kwargs)
        return self.jobs

    async def get(self, job_id: str) -> Job:
        if not self.jobs:
            raise HTTP_404_NOT_FOUND("no job")
        return self.jobs[0]


class FakeAudit:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []
        self.calls: list[dict[str, Any]] = []
        self.error: Optional[Exception] = None
        self.names: dict[str, str] = {}
        self.actors: list[Facet] = []
        self.actions: list[Facet] = []
        self.targets: list[Facet] = []

    async def page(self, **kwargs: Any) -> AuditPage:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return AuditPage(events=self.events[:kwargs["limit"]], actors=self.actors, actions=self.actions,
                         targets=self.targets)

    async def names_for(self, events: list[AuditEvent]) -> dict[str, str]:
        return self.names


@pytest.fixture(autouse=True)
def reset_overrides() -> Any:
    yield
    import main

    for name in ("jobService", "auditService", "pterodactylService", "modpackService", "modrinthService"):
        getattr(main.container, name).reset_override()


MODPACK = FakeModpack()


def reset_modpack() -> None:
    MODPACK.preview_error = None
    MODPACK.calls.clear()


def setup(actor: Optional[Actor] = EDITOR) -> tuple[TestClient, FakeJobs, FakeAudit, FakePterodactyl]:
    import main

    jobs, audit, ptero = FakeJobs(), FakeAudit(), FakePterodactyl()
    reset_modpack()
    main.container.modpackService.override(providers.Object(MODPACK))
    main.container.modrinthService.override(providers.Object(FakeModrinth()))
    main.container.jobService.override(providers.Object(jobs))
    main.container.auditService.override(providers.Object(audit))
    main.container.pterodactylService.override(providers.Object(ptero))
    client = TestClient(main.app, base_url="https://testserver", follow_redirects=False)
    if actor is not None:
        client.cookies.set("session", session_cookie(session_for(actor)))
    return client, jobs, audit, ptero


def test_active_job_row_keeps_polling() -> None:
    client, jobs, *_ = setup()
    jobs.jobs = [make_job(status="running", active=True)]
    response = client.get(f"/jobs/{jobs.jobs[0].id}/row")
    assert response.status_code == 200 and "every 2s" in response.text


def test_finished_job_row_stops_polling() -> None:
    client, jobs, *_ = setup()
    jobs.jobs = [make_job(status="done", active=False)]
    response = client.get(f"/jobs/{jobs.jobs[0].id}/row")
    assert response.status_code == 200 and "every 2s" not in response.text and "hx-get" not in response.text


def test_finished_row_triggers_a_refresh_and_an_active_one_does_not() -> None:
    """A finished row tells the page (`job-finished`, which #server-main listens for) once: it
    does not poll again. An active one never does, or the page would reload every 2 s."""
    client, jobs, *_ = setup()
    jobs.jobs = [make_job(status="done", active=False)]
    finished = client.get(f"/jobs/{jobs.jobs[0].id}/row")
    jobs.jobs = [make_job(status="running", active=True)]
    active = client.get(f"/jobs/{jobs.jobs[0].id}/row")
    assert finished.headers["HX-Trigger"] == "job-finished" and "HX-Trigger" not in active.headers
    jobs.jobs = [make_job(status="failed", active=False, error="boom")]
    assert client.get(f"/jobs/{jobs.jobs[0].id}/row").headers["HX-Trigger"] == "job-finished"


def test_job_row_shows_requested_by() -> None:
    client, jobs, *_ = setup()
    jobs.jobs = [make_job(requested_by=ActorRef(iss="i", sub="s", name="Ana"))]
    assert "Ana" in client.get(f"/jobs/{jobs.jobs[0].id}/row").text
    jobs.jobs = [make_job(requested_by=None)]
    text = client.get(f"/jobs/{jobs.jobs[0].id}/row").text
    assert "programado" in text


def test_unknown_job_row_is_a_not_found_notice() -> None:
    client, *_ = setup()
    assert client.get(f"/jobs/{PydanticObjectId()}/row").status_code == 404


def test_operator_row_of_a_finished_dry_run_has_the_real_run_form_with_the_server_name() -> None:
    client, jobs, _, ptero = setup(OPERATOR)
    jobs.jobs = [finished_dry_run()]
    text = client.get(f"/jobs/{jobs.jobs[0].id}/row").text
    assert f'hx-post="/servers/{SERVER_ID}/update"' in text
    assert ptero.server.name in text


def test_editor_row_of_a_finished_dry_run_has_a_disabled_button() -> None:
    client, jobs, *_ = setup(EDITOR)
    jobs.jobs = [finished_dry_run()]
    text = client.get(f"/jobs/{jobs.jobs[0].id}/row").text
    assert "/update" not in text and "requiere operador" in text


def test_row_tolerates_a_missing_server() -> None:
    client, jobs, _, ptero = setup(OPERATOR)
    jobs.jobs = [finished_dry_run()]

    async def gone(server_id: str) -> Any:
        raise HTTP_404_NOT_FOUND("no server")

    ptero.server_get = gone  # type: ignore[method-assign]
    response = client.get(f"/jobs/{jobs.jobs[0].id}/row")
    assert response.status_code == 200 and "Ejecutar de verdad" in response.text


def test_jobs_page_lists_recent_jobs_with_who_asked() -> None:
    client, jobs, *_ = setup()
    jobs.jobs = [make_job(requested_by=ActorRef(iss="i", sub="s", name="Ana")), make_job(requested_by=None)]
    response = client.get("/jobs")
    assert response.status_code == 200 and "Ana" in response.text and "programado" in response.text
    assert jobs.calls == [{"server_id": None, "kind": None, "status": None, "before_id": None, "limit": 51}]


def test_jobs_table_links_each_row_to_its_page_and_names_the_server() -> None:
    client, jobs, _, ptero = setup()
    jobs.jobs = [make_job(requested_by=ActorRef(iss="i", sub="s", name="Ana"))]
    text = client.get("/jobs").text
    assert f'href="/jobs/{jobs.jobs[0].id}"' in text
    assert f'href="/servers/{SERVER_ID}"' in text and ptero.server.name in text
    for header in ("Tipo", "Servidor", "Estado", "Paso", "Pedido por", "Creado"):
        assert header in text


def _summary_of(text: str, job_id: Any) -> str:
    """The `<summary>` of the card of job `job_id` in `text`."""
    start = text.index(f'data-job="{job_id}"')
    begin = text.index("<summary", start)
    return text[begin:text.index("</summary>", begin) + len("</summary>")]


def test_jobs_are_cards_whose_summary_is_the_old_row() -> None:
    client, jobs, _, ptero = setup()
    jobs.jobs = [make_job(status="running", active=True, requested_by=ActorRef(iss="i", sub="s", name="Ana"))]
    text = client.get("/jobs").text
    assert f'<details data-job="{jobs.jobs[0].id}"' in text and "<table" not in text
    summary = " ".join(visible(_summary_of(text, jobs.jobs[0].id)).split())
    for part in ("Escaneo", ptero.server.name, "En curso", "Revisando", "Ana"):
        assert part in summary


def test_opening_a_job_card_loads_its_detail_and_closing_it_lets_it_go() -> None:
    """Final review of batch A (minor 5): the card had the id of the row it loads (job-<id>, twice
    on the page), and the loaded row kept polling after the card was closed, beside the line's own
    poll. The card is job-card-<id>; app.js loads the detail on open and empties it on close."""
    client, jobs, *_ = setup()
    jobs.jobs = [make_job(status="done", active=False)]
    job_id = jobs.jobs[0].id
    text = client.get("/jobs").text
    details = text[text.index(f'<details data-job="{job_id}"'):]
    details = details[:details.index(">")]
    assert f'id="job-card-{job_id}"' in details and f'data-job-detail="/jobs/{job_id}/row"' in details
    assert "hx-trigger" not in details and "hx-get" not in details
    assert f'id="job-{job_id}"' not in text
    body = text[text.index("</summary>", text.index(f'data-job="{job_id}"')):text.index("</details>")]
    assert f'href="/jobs/{job_id}"' in body and "Abrir página" in body and str(job_id) in body


def test_an_active_job_polls_only_its_summary() -> None:
    client, jobs, *_ = setup()
    jobs.jobs = [make_job(status="running", active=True)]
    job_id = jobs.jobs[0].id
    text = client.get("/jobs").text
    summary = _summary_of(text, job_id)
    assert f'hx-get="/jobs/{job_id}/summary"' in summary and 'hx-trigger="every 2s"' in summary
    assert 'hx-swap="outerHTML"' in summary and 'hx-target="this"' in summary
    assert text.count("every 2s") == 1  # the card itself does not poll, so an open one stays open
    jobs.jobs = [make_job(status="done", active=False)]
    text = client.get("/jobs").text
    assert "every 2s" not in text and "/summary\"" not in text


def test_job_summary_route_answers_the_summary_alone() -> None:
    client, jobs, *_ = setup()
    jobs.jobs = [make_job(status="running", active=True)]
    response = client.get(f"/jobs/{jobs.jobs[0].id}/summary")
    assert response.status_code == 200 and response.text.lstrip().startswith("<summary")
    assert "<details" not in response.text and "every 2s" in response.text
    jobs.jobs = [make_job(status="done", active=False)]
    assert "every 2s" not in client.get(f"/jobs/{jobs.jobs[0].id}/summary").text


def test_the_old_row_route_tells_a_page_from_before_to_stop_polling() -> None:
    """Final review of batch A (minor 6): a page left open across the deploy polls /jobs/{id}/tr,
    gone since. htmx stops polling on 286; a 404 would put an error notice up every 2 s."""
    client, jobs, *_ = setup()
    jobs.jobs = [make_job(status="running", active=True)]
    response = client.get(f"/jobs/{jobs.jobs[0].id}/tr")
    assert response.status_code == 286 and response.text == ""


def test_jobs_filters_pass_through_as_exact_values() -> None:
    client, jobs, *_ = setup()
    client.get("/jobs", params={"servidor": ".*", "tipo": "server_*", "estado": "done", "before": "abc"})
    assert jobs.calls[-1] == {"server_id": ".*", "kind": "server_*", "status": "done", "before_id": "abc", "limit": 51}
    client.get("/jobs", params={"servidor": "", "tipo": "", "estado": ""})
    assert jobs.calls[-1]["server_id"] is None and jobs.calls[-1]["kind"] is None and jobs.calls[-1]["status"] is None


def test_jobs_filter_form_has_selects_with_the_known_values() -> None:
    client, jobs, _, ptero = setup()
    text = client.get("/jobs", params={"estado": "failed", "servidor": str(SERVER_ID)}).text
    for name in ("servidor", "tipo", "estado"):
        assert f'<select id="f-{name}" name="{name}"' in text
    for value in ("server_scan", "server_update", "server_rollback", "notify", "queued", "running", "done", "failed"):
        assert f'value="{value}"' in text
    assert f'value="{SERVER_ID}" selected' in text and 'value="failed" selected' in text


def test_jobs_paginate_keeping_the_filters() -> None:
    client, jobs, *_ = setup()
    jobs.jobs = [make_job() for _ in range(51)]
    text = client.get("/jobs", params={"estado": "done"}).text
    assert f"before={jobs.jobs[49].id}" in text and "estado=done" in text and "anteriores" in text
    jobs.jobs = [make_job()]
    assert "anteriores" not in client.get("/jobs").text


def test_jobs_unknown_before_is_a_not_found_notice() -> None:
    client, jobs, *_ = setup()

    async def bad(**kwargs: Any) -> list[Job]:
        raise HTTP_404_NOT_FOUND("bad cursor")

    jobs.find = bad  # type: ignore[method-assign]
    assert client.get("/jobs", params={"before": "zzz"}).status_code == 404


def test_job_page_shows_the_detail_with_the_plan_and_the_real_run_for_an_operator() -> None:
    client, jobs, _, ptero = setup(OPERATOR)
    jobs.jobs = [finished_dry_run()]
    response = client.get(f"/jobs/{jobs.jobs[0].id}")
    assert response.status_code == 200 and "a.jar" in response.text
    assert f'hx-post="/servers/{SERVER_ID}/update"' in response.text and ptero.server.name in response.text


def test_job_page_gives_an_editor_a_disabled_button() -> None:
    client, jobs, *_ = setup(EDITOR)
    jobs.jobs = [finished_dry_run()]
    text = client.get(f"/jobs/{jobs.jobs[0].id}").text
    assert "/update" not in text and "requiere operador" in text


def test_job_page_polls_while_active() -> None:
    client, jobs, *_ = setup()
    jobs.jobs = [make_job(status="running", active=True)]
    assert f'hx-get="/jobs/{jobs.jobs[0].id}/row"' in client.get(f"/jobs/{jobs.jobs[0].id}").text


def test_unknown_job_page_is_a_not_found_notice() -> None:
    client, *_ = setup()
    assert client.get(f"/jobs/{PydanticObjectId()}").status_code == 404


def test_audit_filters_pass_through_as_exact_values() -> None:
    client, _, audit, _ = setup()
    audit.events = [event()]
    response = client.get("/audit", params={"actor": ".*", "action": "server.scan", "target": "server:t1"})
    assert response.status_code == 200
    [call] = audit.calls
    assert call["actor_sub"] == ".*" and call["action"] == "server.scan"
    assert call["target"] == ("server", "t1") and call["target_name"] is None
    assert call["before_id"] is None and call["limit"] == 51


def test_audit_paginates_with_before() -> None:
    client, _, audit, _ = setup()
    audit.events = [event() for _ in range(51)]
    response = client.get("/audit")
    last = audit.events[49].id
    assert f"before={last}" in response.text and "anteriores" in response.text
    client.get("/audit", params={"before": str(last)})
    assert audit.calls[-1]["before_id"] == str(last)
    audit.events = [event()]
    assert "anteriores" not in client.get("/audit").text


def test_audit_unknown_before_is_a_not_found_notice() -> None:
    client, _, audit, _ = setup()
    audit.error = HTTP_404_NOT_FOUND("bad cursor")
    assert client.get("/audit", params={"before": "zzz"}).status_code == 404


def test_audit_details_are_escaped() -> None:
    client, _, audit, _ = setup()
    audit.events = [event(details={"note": "<script>alert(1)</script>"})]
    text = client.get("/audit").text
    assert "<script>alert(1)" not in text and "&lt;script&gt;" in text


def test_pages_redirect_to_the_login_without_a_session() -> None:
    client, *_ = setup(None)
    for path in ("/jobs", f"/jobs/{PydanticObjectId()}", f"/jobs/{PydanticObjectId()}/row",
                 f"/jobs/{PydanticObjectId()}/summary", "/audit"):
        assert client.get(path).status_code == 303


def test_audit_dates_become_an_inclusive_utc_range() -> None:
    client, _, audit, _ = setup()
    audit.events = [event()]
    response = client.get("/audit", params={"desde": "2026-10-01", "hasta": "2026-10-02"})
    assert response.status_code == 200
    call = audit.calls[-1]
    assert call["since"] == datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert call["until"] == datetime(2026, 10, 3, tzinfo=timezone.utc)
    assert 'name="desde"' in response.text and 'name="hasta"' in response.text
    assert 'value="2026-10-01"' in response.text and 'value="2026-10-02"' in response.text
    assert "(UTC)" in response.text


def test_audit_without_dates_has_no_range() -> None:
    client, _, audit, _ = setup()
    client.get("/audit", params={"desde": "", "hasta": ""})
    assert audit.calls[-1]["since"] is None and audit.calls[-1]["until"] is None


def test_audit_same_day_range_is_valid() -> None:
    client, _, audit, _ = setup()
    assert client.get("/audit", params={"desde": "2026-10-01", "hasta": "2026-10-01"}).status_code == 200
    assert audit.calls[-1]["until"] == datetime(2026, 10, 2, tzinfo=timezone.utc)


@pytest.mark.parametrize("params", [
    {"desde": "2026-10-05", "hasta": "2026-10-01"},
    {"desde": "mañana"},
    {"hasta": "2026-13-45"},
    {"desde": "2026-02-30"},
    {"hasta": "9999-12-31"},
])
def test_audit_bad_dates_are_a_422_notice_never_a_500(params: dict[str, str]) -> None:
    client, _, audit, _ = setup()
    response = client.get("/audit", params=params)
    assert response.status_code == 422
    assert audit.calls == []
    htmx = client.get("/audit", params=params, headers={"HX-Request": "true"})
    assert htmx.status_code == 422


def test_audit_older_link_keeps_the_dates() -> None:
    client, _, audit, _ = setup()
    audit.events = [event() for _ in range(51)]
    text = client.get("/audit", params={"desde": "2026-10-01", "hasta": "2026-10-02"}).text
    assert "desde=2026-10-01" in text and "hasta=2026-10-02" in text


@pytest.mark.parametrize("path", ["", "/row"])
def test_the_real_run_from_a_job_warns_when_a_rollback_is_pending(path: str) -> None:
    """R17: a real update discards the pending rollback; the dialog says so before confirming."""
    client, jobs, *_ = setup(OPERATOR)
    jobs.jobs = [finished_dry_run()]
    text = client.get(f"/jobs/{jobs.jobs[0].id}{path}").text
    assert "Ejecutar de verdad" in text and PENDING_ROLLBACK not in text
    jobs.state = RollbackState(update=failed_real_update({"backup": {}}), allowed=["B"])
    text = client.get(f"/jobs/{jobs.jobs[0].id}{path}").text
    assert PENDING_ROLLBACK in text and text.index(PENDING_ROLLBACK) < text.index('name="confirm"')
    assert jobs.asked_state[-1] == str(SERVER_ID)


def test_older_link_only_when_more_exist() -> None:
    client, jobs, audit, _ = setup()
    for count, has_link in ((50, False), (51, True)):
        jobs.jobs = [make_job() for _ in range(count)]
        jobs.calls.clear()
        text = client.get("/jobs").text
        assert jobs.calls[0]["limit"] == 51
        assert ("Ver anteriores" in text) is has_link
        if has_link:
            assert f"before={jobs.jobs[49].id}" in text and str(jobs.jobs[50].id) not in text
        audit.events = [event() for _ in range(count)]
        audit.calls.clear()
        text = client.get("/audit").text
        assert audit.calls[0]["limit"] == 51
        assert ("Ver anteriores" in text) is has_link
        if has_link:
            assert f"before={audit.events[49].id}" in text


@pytest.mark.parametrize("path", ["", "/row"])
def test_a_finished_dry_run_loads_its_changes_lazily(path: str) -> None:
    client, jobs, *_ = setup(EDITOR)
    jobs.jobs = [finished_dry_run()]
    text = client.get(f"/jobs/{jobs.jobs[0].id}{path}").text
    assert MODPACK.calls == []  # the page does not wait for Modrinth
    assert f'hx-get="/jobs/{jobs.jobs[0].id}/preview"' in text and 'hx-trigger="load"' in text
    assert "Cargando cambios" in text and 'id="changes"' not in text
    # The change block first; the jar lists folded under it (F33).
    assert text.index("Cargando cambios") < text.index("<details") < text.index("Se instalan")


def test_the_preview_of_a_finished_dry_run_is_the_changes_of_the_build_it_simulated() -> None:
    client, jobs, *_ = setup(EDITOR)  # a read: an editor loads it too
    jobs.jobs = [finished_dry_run()]
    response = client.get(f"/jobs/{jobs.jobs[0].id}/preview")
    assert response.status_code == 200 and ("server_change_preview", str(SERVER_ID), 3) in MODPACK.calls
    assert 'id="changes"' in response.text and "Arregla un crash" in response.text and "Sodium" in response.text
    assert "<html" not in response.text


def test_the_preview_of_a_dry_run_without_a_scan_says_so_instead_of_the_changes() -> None:
    from src.exceptions import HTTP_409_CONFLICT

    client, jobs, *_ = setup(EDITOR)
    MODPACK.preview_error = HTTP_409_CONFLICT("Escanea el servidor antes de comparar")
    jobs.jobs = [finished_dry_run()]
    response = client.get(f"/jobs/{jobs.jobs[0].id}/preview")
    assert response.status_code == 200 and "Escanea el servidor antes de comparar" in response.text
    assert 'id="changes"' not in response.text


@pytest.mark.parametrize("make", [
    lambda: make_job("server_update", payload={"server_id": str(SERVER_ID), "dry_run": True}),  # still active
    lambda: make_job("server_scan", status="done", active=False, payload={"server_id": str(SERVER_ID)}),
    lambda: make_job("server_update", status="done", active=False, payload={"server_id": str(SERVER_ID), "dry_run": False},
                     checkpoint=PLAN),
])
def test_the_preview_of_anything_but_a_finished_dry_run_is_a_not_found_notice(make: Any) -> None:
    client, jobs, *_ = setup(EDITOR)
    jobs.jobs = [make()]
    response = client.get(f"/jobs/{jobs.jobs[0].id}/preview")
    assert response.status_code == 404 and "data-notice" in response.text and MODPACK.calls == []


def test_job_steps_read_in_spanish() -> None:
    client, jobs, *_ = setup()
    jobs.jobs = [make_job(status="running", active=True)]
    jobs.jobs[0].step = "drift"
    text = client.get(f"/jobs/{jobs.jobs[0].id}/row").text
    assert "Comparando" in text and ">drift<" not in text
    tr = client.get(f"/jobs/{jobs.jobs[0].id}/summary").text
    assert "Comparando" in tr and ">drift<" not in tr
    jobs.jobs[0].step = "paso-raro"
    assert "paso-raro" in client.get(f"/jobs/{jobs.jobs[0].id}/row").text


def test_job_steps_cover_every_service_step() -> None:
    import re
    from pathlib import Path
    from src.web import templates
    steps = set()
    for path in Path("src/services").glob("*.py"):
        steps |= set(re.findall(r'step\("([a-z_]+)"', path.read_text()))
    labels = getattr(templates.env.get_template("_macros.html").module, "JOB_STEP")
    assert steps and steps <= set(labels)



def test_a_finished_import_row_carries_its_full_report() -> None:
    # Owner (2026-10-12, decision 3a): the server's page keeps only what is pending; the job's row
    # says what the import did.
    client, jobs, *_ = setup()
    report = {"added": ["P1", "P2"], "candidates": [], "local": ["/mods/own.jar"], "too_large": [],
              "on_server": ["P1"], "channel_exceptions": {}}
    jobs.jobs = [make_job("server_import", status="done", active=False, payload={"server_id": str(SERVER_ID)},
                          checkpoint={"import": report})]
    shown = " ".join(visible(client.get(f"/jobs/{jobs.jobs[0].id}/row").text).split())
    assert "Mods agregados: 2" in shown and "Archivos locales registrados: 1" in shown
    # The added mods by name, from one catalog fill (code review, 2026-10-12: they showed as ids).
    assert "Sodium" in shown and "Lithium" in shown
    # B5: «Marcados «En el servidor»: N · Aceptan beta: N», there too.
    assert "Marcados «En el servidor»: 1 · Aceptan beta: 0" in shown


def test_a_finished_import_row_counts_its_pending_when_it_is_read() -> None:
    """Review of release 0.3.0: the row said «Pendientes de tu decisión (N)» as the import left it;
    the server's page lowers N as the admin decides, and so does the row now."""
    client, jobs, _, ptero = setup()
    candidate: dict[str, Any] = {"path": "/mods/a.jar", "sha512": "s1", "metadata": None, "candidates": []}
    report = {"added": [], "candidates": [candidate, {**candidate, "path": "/mods/b.jar", "sha512": "s2"}],
              "local": [], "too_large": [], "on_server": [], "channel_exceptions": {}}
    jobs.jobs = [make_job("server_import", status="done", active=False, payload={"server_id": str(SERVER_ID)},
                          checkpoint={"import": report})]
    ptero.decided = {"s1"}
    shown = " ".join(visible(client.get(f"/jobs/{jobs.jobs[0].id}/row").text).split())
    assert "Pendientes de tu decisión ( 1 )" in shown  # the number in <strong>, spaced by visible()


# The audit log as cards, in Spanish (A9).

def _audit_card(text: str) -> tuple[str, str]:
    """The first audit card of the page: its <summary> and its body."""
    begin = text.index("<summary", text.index("data-audit="))
    middle = text.index("</summary>", begin)
    return text[begin:middle], text[middle:text.index("</details>", middle)]


def test_an_audit_card_shows_when_who_what_and_the_target_by_name() -> None:
    client, _, audit, _ = setup()
    audit.events = [event(name="Olga", action="bundle.pin", target=AuditTarget(kind="bundle", id="b1", name="Survival"))]
    text = client.get("/audit").text
    assert "<table" not in text
    summary = " ".join(visible(_audit_card(text)[0]).split())
    assert "Olga" in summary and "Fijó una versión" in summary and "Pack «Survival»" in summary
    assert "2026-10-01 12:00" in summary and "bundle.pin" not in summary


def test_an_open_audit_card_translates_the_details_and_ends_with_the_ids() -> None:
    client, _, audit, _ = setup()
    audit.names = {"J": "JEI", "V": "19.51"}
    audit.events = [event(action="bundle.pin", target=AuditTarget(kind="bundle", id="b1", name="Survival"),
                          details={"project_id": "J", "version_id": "V", "downgrade": {"from": "19.57", "to": "19.51"}})]
    body = " ".join(visible(_audit_card(client.get("/audit").text)[1]).split())
    assert "Mod JEI (J)" in body and "Versión 19.51 (V)" in body and "Baja 19.57 → 19.51, confirmada" in body
    ids = "bundle.pin · objetivo bundle:b1 · persona s · petición r1"
    assert ids in body and body.index(ids) > body.index("Baja")


def test_an_unknown_key_or_id_is_shown_raw() -> None:
    client, _, audit, _ = setup()
    audit.events = [event(action="bundle.pin", details={"foo": 1, "project_id": "ZZZ"})]
    response = client.get("/audit")
    assert response.status_code == 200
    body = " ".join(visible(_audit_card(response.text)[1]).split())
    assert "foo 1" in body and "Mod ZZZ" in body


def test_an_unknown_action_is_shown_raw() -> None:
    client, _, audit, _ = setup()
    audit.events = [event(action="server.power")]
    assert "server.power" in visible(_audit_card(client.get("/audit").text)[0])


def test_an_event_without_a_target_name_shows_its_kind_and_id() -> None:
    client, _, audit, _ = setup()
    audit.events = [event(target=AuditTarget(kind="server", id="t1")),
                    event(target=AuditTarget(kind="job", id="j9", name=""))]
    text = " ".join(visible(client.get("/audit").text).split())
    assert "Servidor t1" in text and "job j9" in text


# The audit filters, with counts (A11, A12).

def _select(text: str, name: str) -> str:
    begin = text.index(f'name="{name}"')
    return text[text.rindex("<select", 0, begin):text.index("</select>", begin)]


def test_person_and_action_are_selects_with_counts() -> None:
    client, _, audit, _ = setup()
    audit.events = [event(name="Olga", action="bundle.pin")]
    audit.actors = [Facet(value="olga", label="Olga", count=3), Facet(value="pedro", label="Pedro", count=1)]
    audit.actions = [Facet(value="bundle.pin", label="bundle.pin", count=2)]
    text = client.get("/audit", params={"actor": "olga"}).text
    person = _select(text, "actor")
    assert '<option value="olga" selected' in person and "Olga (3)" in person and "Pedro (1)" in person
    action = _select(text, "action")
    for area in ("Packs", "Builds", "Servidores", "Reemplazos"):
        assert f'<optgroup label="{area}">' in action
    assert "Fijó una versión (2)" in action
    zero = action[action.rindex("<option", 0, action.index("Creó un pack (0)")):action.index("Creó un pack (0)")]
    assert 'value="bundle.create"' in zero and "text-zinc-400" in zero and "disabled" not in zero


def test_target_is_an_input_with_a_datalist_of_names_kinds_and_counts() -> None:
    client, _, audit, _ = setup()
    bundle = str(PydanticObjectId())
    audit.targets = [Facet(value=f"bundle:{bundle}", label="Survival", count=31),
                     Facet(value="server:s1", label="server:s1", count=2)]
    text = client.get("/audit").text
    assert re.search(r'<input[^>]*name="target"[^>]*list="audit-targets"', text)
    datalist = text[text.index('<datalist id="audit-targets"'):text.index("</datalist>")]
    # The owner, 2026-10-13 (minor 7 of the final review): the field shows what was chosen by name.
    assert '<option value="Survival · pack">(31)</option>' in datalist
    assert '<option value="s1 · servidor">(2)</option>' in datalist
    assert f"bundle:{bundle}" not in datalist


def test_a_chosen_target_filters_by_its_name_and_kind() -> None:
    client, _, audit, _ = setup()
    response = client.get("/audit", params={"target": "Survival · pack"})
    assert response.status_code == 200 and 'value="Survival · pack"' in response.text
    call = audit.calls[-1]
    assert call["target"] is None and call["target_name"] == "Survival" and call["target_kind"] == "bundle"


def test_an_unknown_action_or_a_plain_target_name_does_not_422() -> None:
    client, _, audit, _ = setup()
    response = client.get("/audit", params={"action": "foo", "target": "Survival"})
    assert response.status_code == 200
    call = audit.calls[-1]
    assert call["action"] is None and call["target"] is None and call["target_name"] == "Survival"
    assert call["target_kind"] is None


def test_the_older_link_keeps_every_filter() -> None:
    client, _, audit, _ = setup()
    audit.events = [event() for _ in range(51)]
    text = client.get("/audit", params={"actor": "olga", "action": "bundle.pin", "target": "bundle:b1"}).text
    link = text[text.index("/audit?before="):]
    link = link[:link.index('"')]
    assert "actor=olga" in link and "action=bundle.pin" in link and "target=bundle%3Ab1" in link


def test_the_filters_ask_htmx_for_the_list_and_keep_a_submit_button() -> None:
    client, _, audit, _ = setup()
    audit.events = [event()]
    text = client.get("/audit").text
    form = text[text.index('<form method="get" action="/audit"'):]
    form = form[:form.index(">")]
    for attribute in ('hx-get="/audit"', 'hx-trigger="change"', 'hx-select="#audit-list"', 'hx-target="#audit-list"',
                      'hx-swap="outerHTML"', 'hx-push-url="true"'):
        assert attribute in form
    region = text[text.index('id="audit-list"'):]
    assert region.index('name="actor"') < region.index("data-audit=") and "Filtrar" in region


def test_the_server_column_of_a_job_card_is_the_widest() -> None:
    """The owner, 2026-10-13, using the demo: a long server name wrapped over many lines in Trabajos."""
    parts = Path("src/web/templates/activity/_job_parts.html").read_text()
    cols = re.search(r"sm:grid-cols-\[([^\]]+)\]", parts)
    assert cols is not None
    shares = [float(f) for f in re.findall(r"minmax\(0,([0-9.]+)fr\)", cols.group(1))]
    assert shares[1] == max(shares) and shares[1] >= 2 * min(shares)  # Tipo, Servidor, Estado, Paso, Pedido por
