"""Jobs and audit pages of the panel (stage 4): reads only, with doubles for the services."""
from datetime import datetime, timezone
from typing import Any, Optional

import pytest
from beanie import PydanticObjectId
from dependency_injector import providers
from fastapi.testclient import TestClient

from src.exceptions import HTTP_404_NOT_FOUND
from src.model.audit import AuditEvent, AuditTarget
from src.model.jobs import Job, RollbackState
from src.model.users import Actor
from src.model.users.actor import ActorRef
from tests._actors import EDITOR, OPERATOR, session_for
from tests._session import session_cookie
from tests.test_web_servers import FakePterodactyl, PENDING_ROLLBACK, SERVER_ID, failed_real_update, job as make_job

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
PLAN = {"preflight": {"build": 3, "bundle": "Pack Uno", "install": [{"filename": "a.jar"}], "remove": []}}


def finished_dry_run() -> Job:
    return make_job("server_update", status="done", active=False,
                    payload={"server_id": str(SERVER_ID), "dry_run": True}, checkpoint=PLAN,
                    requested_by=ActorRef(iss="i", sub="s", name="Ana"))


def event(name: str = "Ana", action: str = "server.scan", details: Optional[dict[str, Any]] = None) -> AuditEvent:
    return AuditEvent.model_construct(
        id=PydanticObjectId(), at=T0, actor=ActorRef(iss="i", sub="s", name=name), action=action,
        target=AuditTarget(kind="server", id="t1"), details=details or {}, request_id="r1")


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

    async def find(self, **kwargs: Any) -> list[AuditEvent]:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.events


@pytest.fixture(autouse=True)
def reset_overrides() -> Any:
    yield
    import main

    for name in ("jobService", "auditService", "pterodactylService"):
        getattr(main.container, name).reset_override()


def setup(actor: Optional[Actor] = EDITOR) -> tuple[TestClient, FakeJobs, FakeAudit, FakePterodactyl]:
    import main

    jobs, audit, ptero = FakeJobs(), FakeAudit(), FakePterodactyl()
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
    assert jobs.calls == [{"server_id": None, "kind": None, "status": None, "before_id": None, "limit": 50}]


def test_jobs_table_links_each_row_to_its_page_and_names_the_server() -> None:
    client, jobs, _, ptero = setup()
    jobs.jobs = [make_job(requested_by=ActorRef(iss="i", sub="s", name="Ana"))]
    text = client.get("/jobs").text
    assert f'href="/jobs/{jobs.jobs[0].id}"' in text
    assert f'href="/servers/{SERVER_ID}"' in text and ptero.server.name in text
    for header in ("Tipo", "Servidor", "Estado", "Paso", "Pedido por", "Creado"):
        assert header in text


def test_jobs_table_refreshes_only_the_active_rows() -> None:
    client, jobs, *_ = setup()
    jobs.jobs = [make_job(status="running", active=True)]
    text = client.get("/jobs").text
    assert f'hx-get="/jobs/{jobs.jobs[0].id}/tr"' in text and 'hx-trigger="every 2s"' in text
    assert 'hx-swap="outerHTML"' in text
    jobs.jobs = [make_job(status="done", active=False)]
    text = client.get("/jobs").text
    assert "every 2s" not in text and '/tr"' not in text


def test_jobs_filters_pass_through_as_exact_values() -> None:
    client, jobs, *_ = setup()
    client.get("/jobs", params={"servidor": ".*", "tipo": "server_*", "estado": "done", "before": "abc"})
    assert jobs.calls[-1] == {"server_id": ".*", "kind": "server_*", "status": "done", "before_id": "abc", "limit": 50}
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
    jobs.jobs = [make_job() for _ in range(50)]
    text = client.get("/jobs", params={"estado": "done"}).text
    assert f"before={jobs.jobs[-1].id}" in text and "estado=done" in text and "anteriores" in text
    jobs.jobs = [make_job()]
    assert "anteriores" not in client.get("/jobs").text


def test_jobs_unknown_before_is_a_not_found_notice() -> None:
    client, jobs, *_ = setup()

    async def bad(**kwargs: Any) -> list[Job]:
        raise HTTP_404_NOT_FOUND("bad cursor")

    jobs.find = bad  # type: ignore[method-assign]
    assert client.get("/jobs", params={"before": "zzz"}).status_code == 404


def test_job_tr_fragment_polls_while_active_and_stops_when_done() -> None:
    client, jobs, *_ = setup()
    jobs.jobs = [make_job(status="running", active=True)]
    response = client.get(f"/jobs/{jobs.jobs[0].id}/tr")
    assert response.status_code == 200 and "every 2s" in response.text and response.text.lstrip().startswith("<tr")
    jobs.jobs = [make_job(status="done", active=False)]
    assert "every 2s" not in client.get(f"/jobs/{jobs.jobs[0].id}/tr").text


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
    response = client.get("/audit", params={"actor": ".*", "action": "server.*", "target": "t1"})
    assert response.status_code == 200
    [call] = audit.calls
    assert call["actor_sub"] == ".*" and call["action"] == "server.*" and call["target_id"] == "t1"
    assert call["before_id"] is None and call["limit"] == 50


def test_audit_paginates_with_before() -> None:
    client, _, audit, _ = setup()
    audit.events = [event() for _ in range(50)]
    response = client.get("/audit")
    last = audit.events[-1].id
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
                 f"/jobs/{PydanticObjectId()}/tr", "/audit"):
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
    audit.events = [event() for _ in range(50)]
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
