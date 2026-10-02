"""Jobs and audit pages of the panel (stage 4): reads only, with doubles for the services."""
from datetime import datetime, timezone
from typing import Any, Optional

import pytest
from beanie import PydanticObjectId
from dependency_injector import providers
from fastapi.testclient import TestClient

from src.exceptions import HTTP_404_NOT_FOUND
from src.model.audit import AuditEvent, AuditTarget
from src.model.jobs import Job
from src.model.users import Actor
from src.model.users.actor import ActorRef
from tests._actors import EDITOR, OPERATOR, session_for
from tests._session import session_cookie
from tests.test_web_servers import FakePterodactyl, SERVER_ID, job as make_job

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
        self.limits: list[int] = []

    async def recent(self, limit: int = 10) -> list[Job]:
        self.limits.append(limit)
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
    assert jobs.limits == [50]


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
    for path in ("/jobs", f"/jobs/{PydanticObjectId()}/row", "/audit"):
        assert client.get(path).status_code == 303
