"""La API dice si está viva y si puede atender (i-7985ee-90f944), sin sesión.

`/health/live` responde mientras el proceso atienda requests; `/health/ready`, solo si MongoDB
contesta. El healthcheck de la API en el compose usa `ready`.
"""
from typing import Any

import pytest
from dependency_injector import providers
from fastapi.testclient import TestClient

from src.database.health import HealthDatabase
from src.services.jobs import WorkerBeat
from tests.conftest import MongoRunner


class FakeHealth:
    def __init__(self, ready: bool) -> None:
        self._ready = ready

    async def ready(self) -> bool:
        return self._ready


def client(ready: bool) -> Any:
    # Sin `with`: el lifespan abriría MongoDB y el proveedor OIDC.
    import main

    main.container.healthService.override(providers.Object(FakeHealth(ready)))
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def reset_overrides() -> Any:
    yield
    import main

    main.container.healthService.reset_override()


def test_live_answers_without_a_session() -> None:
    response = client(ready=False).get("/health/live")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})


def test_ready_answers_200_when_the_database_answers() -> None:
    response = client(ready=True).get("/health/ready")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})


def test_ready_answers_503_when_the_database_does_not() -> None:
    response = client(ready=False).get("/health/ready")
    assert (response.status_code, response.json()) == (503, {"status": "unavailable"})


@pytest.mark.network
def test_the_ping_reaches_a_real_mongodb(mongo: MongoRunner) -> None:
    assert mongo(HealthDatabase().ping) is True


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_beat_without_worker_is_healthy() -> None:
    assert WorkerBeat(clock=FakeClock()).healthy() is True


def test_beat_fresh_is_healthy() -> None:
    clock = FakeClock()
    beat = WorkerBeat(clock=clock)
    beat.expect()
    beat.beat()
    clock.now = 120.0
    assert beat.healthy() is True


def test_beat_stale_is_unhealthy() -> None:
    clock = FakeClock()
    beat = WorkerBeat(clock=clock)
    beat.expect()
    beat.beat()
    clock.now = 121.0
    assert beat.healthy() is False


def test_beat_after_stop_is_unhealthy() -> None:
    clock = FakeClock()
    beat = WorkerBeat(clock=clock)
    beat.expect()
    beat.beat()
    beat.stop()
    assert beat.healthy() is False
