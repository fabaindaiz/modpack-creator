"""El catálogo de un pack como trabajo (spec 2026-10-09-economia-de-requests, E6–E7, F3).

Resolver en el panel nunca espera a Modrinth: con el catálogo al día resuelve desde la base; si le
falta algo deja un trabajo que lo trae, uno solo por pack aunque se pulse de nuevo, y que muestra su
avance. Pasada la ventana, resuelve igual desde la base y deja la revisión en un trabajo. El botón
manual se puede usar una vez por minuto."""
from datetime import timedelta
from typing import Any

import pytest

from src.database.jobs import JobDatabase
from src.exceptions import CatalogPending, QuotaExceeded
from src.model.jobs import Job
from src.services.jobs import JobWorker
from src.services.modpack import CATALOG_WINDOW, MANUAL_REFRESH_EVERY
from tests._actors import EDITOR, RecordingAudit
from tests.conftest import MongoRunner
from tests.test_catalog_freshness import setup

pytestmark = pytest.mark.network


async def run_jobs(service: Any) -> None:
    worker = JobWorker(JobDatabase(), service.handlers(), owner="w1", clock=service.clock)
    while await worker.run_once():
        pass


def test_a_pack_the_catalog_lacks_leaves_one_job_however_many_times_it_is_asked(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        service, api, clock, bundle_id = await setup()
        with pytest.raises(CatalogPending) as first:
            await service.resolve(bundle_id, EDITOR)
        with pytest.raises(CatalogPending) as second:
            await service.resolve(bundle_id, EDITOR)
        jobs = await Job.find().to_list()
        return first.value.job, second.value.job, jobs, api.calls

    first, second, jobs, calls = mongo(scenario)
    assert first.id == second.id and len(jobs) == 1
    assert (jobs[0].kind, jobs[0].key, jobs[0].requested_by.sub) == ("catalog_refresh", f"catalog:{jobs[0].payload['bundle_id']}", EDITOR.sub)
    assert calls == []  # the panel's request never waited for Modrinth


def test_the_job_fetches_saves_the_resolution_and_says_how_far_it_got(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        service, api, clock, bundle_id = await setup()
        with pytest.raises(CatalogPending):
            await service.resolve(bundle_id, EDITOR)
        await run_jobs(service)
        [job] = await Job.find().to_list()
        saved = await service.resolution(bundle_id)
        api.calls.clear()
        lock = await service.resolve(bundle_id, EDITOR)  # inside the window now: from MongoDB
        return job, saved, lock, api.calls

    job, saved, lock, calls = mongo(scenario)
    assert job.status == "done" and job.progress is not None and job.progress.done == job.progress.total
    assert saved is not None and saved[1] is False and {e.project_id for e in saved[0].lock.entries} == {"AAAA", "BBBB", "LIBB"}
    assert {e.project_id for e in lock.entries} == {"AAAA", "BBBB", "LIBB"} and calls == []


def test_past_the_window_the_pack_resolves_at_once_and_the_check_waits_in_a_job(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        service, api, clock, bundle_id = await setup()
        await service.resolve_fetching(bundle_id)
        api.calls.clear()
        clock.now += CATALOG_WINDOW + timedelta(seconds=1)
        lock = await service.resolve(bundle_id, EDITOR)
        return lock, api.calls, [(j.kind, j.status) for j in await Job.find().to_list()]

    lock, calls, jobs = mongo(scenario)
    assert {e.project_id for e in lock.entries} == {"AAAA", "BBBB", "LIBB"}
    assert calls == [] and jobs == [("catalog_refresh", "queued")]


def test_the_manual_refresh_checks_now_once_a_minute_and_shows_the_running_one(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        service, api, clock, bundle_id = await setup()
        service.audit = audit = RecordingAudit()
        await service.resolve_fetching(bundle_id)
        first = await service.catalog_refresh(bundle_id, EDITOR)
        again = await service.catalog_refresh(bundle_id, EDITOR)  # still queued: the same one
        await run_jobs(service)
        api_calls = list(api.calls)
        clock.now += timedelta(seconds=30)
        with pytest.raises(QuotaExceeded) as early:
            await service.catalog_refresh(bundle_id, EDITOR)
        clock.now += MANUAL_REFRESH_EVERY
        later = await service.catalog_refresh(bundle_id, EDITOR)
        return first, again, later, early.value.detail, api_calls, [e[0] for e in audit.events]

    first, again, later, detail, calls, events = mongo(scenario)
    assert first.id == again.id != later.id and first.payload["force"] is True
    assert "Se puede volver a actualizar en 30 s" in str(detail)
    assert ("project_get_many", ("AAAA", "BBBB", "LIBB")) in calls  # inside the window, it checked
    assert events.count("bundle.catalog_refresh") == 2


# El build como trabajo (E2, E9): bloqueado en lo local, se dice al instante; si no, un trabajo
# revisa cambios en Modrinth, resuelve y congela, y dice cómo terminó.


def test_a_build_blocked_locally_is_refused_at_once_without_a_job(mongo: MongoRunner) -> None:
    from src.exceptions import BuildBlocked

    async def scenario() -> tuple[Any, Any]:
        service, api, clock, bundle_id = await setup()
        await service.resolve_fetching(bundle_id)
        api.calls.clear()
        with pytest.raises(BuildBlocked) as blocked:
            await service.build_request(bundle_id, EDITOR)  # nothing decided: every mod is "Por decidir"
        return blocked.value.only_undecided, (api.calls, await Job.find().to_list())

    only_undecided, (calls, jobs) = mongo(scenario)
    assert only_undecided is True and calls == [] and jobs == []


def test_a_buildable_pack_leaves_one_build_job_that_checks_and_freezes(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        service, api, clock, bundle_id = await setup()
        service.audit = audit = RecordingAudit()
        await service.resolve_fetching(bundle_id)
        await service.decisions_set(bundle_id, {"AAAA": True, "BBBB": True, "LIBB": True}, EDITOR)
        first = await service.build_request(bundle_id, EDITOR)
        again = await service.build_request(bundle_id, EDITOR)
        api.calls.clear()
        await run_jobs(service)
        done = await JobDatabase().get(str(first.id))
        bundle = await service.bundle_get(bundle_id)
        return first, again, done, bundle.latest_build, [c[0] for c in api.calls], [e[0] for e in audit.events]

    first, again, done, latest, calls, events = mongo(scenario)
    assert first.id == again.id and (first.kind, first.key) == ("build_create", f"build:{first.payload['bundle_id']}")
    assert done.status == "done" and done.checkpoint["build"] == {"number": 1} and latest == 1
    assert calls == ["project_get_many"]  # the build's check: one batched request, nothing new
    assert "build.request" in events and "build.create" in events


def test_a_build_blocked_by_what_the_check_found_ends_saying_so(mongo: MongoRunner) -> None:
    from tests.test_catalog_freshness import version

    async def scenario() -> Any:
        service, api, clock, bundle_id = await setup()
        await service.resolve_fetching(bundle_id)
        await service.decisions_set(bundle_id, {"AAAA": True, "BBBB": True, "LIBB": True}, EDITOR)
        job = await service.build_request(bundle_id, EDITOR)
        api.versions["b2"] = version("b2", "BBBB", 5, deps=["NEWDEP"])  # a new dependency Modrinth lacks
        await run_jobs(service)
        return (await JobDatabase().get(str(job.id))).checkpoint["build"], await service.last_build_blocked(bundle_id)

    outcome, blocked = mongo(scenario)
    assert outcome["blocked"] is True and outcome["problems"] >= 1 and blocked is True


def test_a_build_of_a_pack_the_catalog_lacks_is_one_click_its_job_brings_what_is_missing(mongo: MongoRunner) -> None:
    # Seen in the visual sweep (2026-10-09): "Crear build" on a pack never resolved left a catalog job
    # and asked for a second click. The build job brings what is missing itself.
    async def scenario() -> tuple[Any, ...]:
        service, api, clock, bundle_id = await setup()
        job = await service.build_request(bundle_id, EDITOR)
        await run_jobs(service)
        done = await JobDatabase().get(str(job.id))
        return job.kind, done.checkpoint.get("build"), await service.last_build_blocked(bundle_id)

    kind, outcome, blocked = mongo(scenario)
    assert kind == "build_create" and outcome["blocked"] is True and blocked is True  # every mod is "Por decidir"
