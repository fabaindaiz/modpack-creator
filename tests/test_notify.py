"""Avisos por webhook de Discord (i-7985ee-34933b).

- El webhook según la documentación de Discord: POST a la URL, `content` de hasta 2000 caracteres,
  `allowed_mentions.parse` vacío para que el texto no mencione a nadie, 204 sin cuerpo.
- Un aviso es un trabajo de la cola: se entrega aunque Discord esté caído un rato, espera lo que
  pide un 429, y no se reenvía si ya salió (su paso tiene checkpoint).
- Un aviso nunca hace fallar lo que avisa (tarjeta best-effort-side-channels).
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, cast

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from src.database.jobs import JobDatabase
from src.library.api.client.discord import DiscordWebhookAPI
from src.model.jobs import Job
from src.services.jobs import JobWorker
from src.services.notify import Notifier, drift_message
from tests._actors import OPERATOR
from tests.conftest import MongoRunner

pytestmark = pytest.mark.network

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


class Webhook:
    """Un webhook de Discord local: registra lo que recibe y contesta lo que se le pida."""

    def __init__(self, answers: list[tuple[int, dict[str, str]]]) -> None:
        self.answers = answers
        self.received: list[dict[str, Any]] = []

    async def handler(self, request: web.Request) -> web.StreamResponse:
        self.received.append({"path": request.path, "query": dict(request.query), "json": await request.json()})
        status, headers = self.answers.pop(0) if self.answers else (204, {})
        if status == 429:
            return web.json_response({"message": "You are being rate limited.", "retry_after": 12.0, "global": False},
                                     status=429, headers=headers)
        return web.Response(status=status)


async def with_webhook(webhook: Webhook, call: Any) -> Any:
    app = web.Application()
    app.router.add_post("/api/webhooks/{id}/{token}", webhook.handler)
    async with TestServer(app) as server:
        return await call(str(server.make_url("/api/webhooks/1/secret-token")))


def test_a_message_is_posted_as_discord_documents_it() -> None:
    webhook = Webhook([])

    async def call(url: str) -> None:
        api = DiscordWebhookAPI()
        await api.send(url, "@everyone " + "x" * 3000)
        await api.close()

    asyncio.run(with_webhook(webhook, call))
    [message] = webhook.received
    assert message["path"] == "/api/webhooks/1/secret-token"
    assert message["json"]["allowed_mentions"] == {"parse": []}
    assert len(message["json"]["content"]) == 2000 and message["json"]["content"].startswith("@everyone")


def test_without_a_webhook_nothing_is_queued(mongo: MongoRunner) -> None:
    async def scenario() -> int:
        await Notifier(JobDatabase(), webhook_url=None).notify("hello")
        return await Job.count()

    assert mongo(scenario) == 0


def test_a_failing_notification_never_fails_the_caller_and_leaves_its_own_line(caplog: pytest.LogCaptureFixture) -> None:
    class BrokenJobs:
        async def enqueue(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("database down")

    with caplog.at_level(logging.WARNING, logger="modpack.notify"):
        asyncio.run(Notifier(cast(Any, BrokenJobs()), webhook_url="https://discord.example/x").notify("hello"))
    assert any(r.name == "modpack.notify" and "notification not queued" in r.getMessage() for r in caplog.records)


def test_a_notification_waits_what_a_429_asks_and_is_sent_once(mongo: MongoRunner) -> None:
    webhook = Webhook([(429, {"Retry-After": "12"})])

    async def scenario(url: str) -> tuple[str, list[str], int]:
        jobs = JobDatabase()
        notifier = Notifier(jobs, webhook_url=url, api=DiscordWebhookAPI())
        await notifier.notify("server drifted")
        clock = {"now": T0}
        worker = JobWorker(jobs, notifier.handlers(), owner="w1", clock=lambda: clock["now"])
        await worker.run_once()
        job = await Job.find_one(Job.kind == "notify")
        assert job is not None and job.status == "queued"
        assert job.available_at is not None
        wait = job.available_at.replace(tzinfo=timezone.utc) - T0
        too_soon = await worker.run_once()
        clock["now"] = T0 + timedelta(seconds=13)
        await worker.run_once()
        await worker.run_once()
        done = await Job.find_one(Job.kind == "notify")
        assert done is not None
        await notifier.close()
        return done.status, [r["json"]["content"] for r in webhook.received], int(wait.total_seconds()) if not too_soon else -1

    status, sent, wait = mongo(lambda: with_webhook(webhook, scenario))
    assert status == "done" and wait == 12
    # Dos intentos llegaron a Discord: el 429 y el que salió. El segundo, una sola vez.
    assert sent == ["server drifted", "server drifted"]


# Qué se avisa.


def test_the_drift_message_says_what_differs() -> None:
    from src.library.scan import Comparison, ExpectedEntry, ScanFile

    old = ScanFile(path="/mods/a.jar", size=1, modified_at=T0, status="identified", project_id="A", version_id="a0")
    hand = ScanFile(path="/mods/hand.jar", size=1, modified_at=T0, status="unknown")
    comparison = Comparison(matching=[], different_version=[(old, ExpectedEntry(project_id="A", version_id="a1", server="include"))],
                            missing=[ExpectedEntry(project_id="C", version_id="c1", server="include")], extra=[], unknown=[hand])
    text = drift_message("Survival", "pack", 3, comparison)
    assert "Survival" in text and "build 3" in text
    assert "/mods/a.jar" in text and "C" in text and "/mods/hand.jar" in text
    assert drift_message("Survival", "pack", 3, Comparison(matching=[old], different_version=[], missing=[], extra=[], unknown=[])) \
        .startswith("✅")


class Recorder:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def notify(self, text: str) -> None:
        self.sent.append(text)

    def handlers(self) -> dict[str, Any]:
        return {}


def test_a_server_drifting_from_its_build_is_announced_once_and_again_when_back(mongo: MongoRunner) -> None:
    from src.database.pterodactyl import PterodactylDatabase
    from src.services.jobs import JobService
    from tests.test_server_scan import FakePanel, HashModrinthAPI, build_with_fabric_api, setup, sha1

    async def scenario() -> list[str]:
        modrinth = HashModrinthAPI({})
        bundle_id, version_id = await build_with_fabric_api(modrinth)
        modrinth.known[sha1(b"fabric api")] = version_id
        panel = FakePanel()
        panel.files = {"fabric-api.jar": (b"fabric api", T0), "handmade.jar": (b"by hand", T0)}
        servers, server_id = await setup(panel, modrinth)
        await servers.server_link_bundle(server_id, bundle_id, actor=OPERATOR)
        recorder = Recorder()
        jobs = JobService(jobDB=JobDatabase(), pterodactylService=servers, pterodactylDB=PterodactylDatabase(),
                          notifier=cast(Any, recorder))
        worker = JobWorker(JobDatabase(), jobs.handlers(), owner="w1", clock=lambda: T0)
        for change in ("drift", "same", "fixed"):
            if change == "fixed":
                del panel.files["handmade.jar"]
            await jobs.enqueue_scan(server_id)
            await worker.run_once()
        return recorder.sent

    sent = mongo(scenario)
    assert len(sent) == 2
    assert "handmade.jar" in sent[0] and sent[1].startswith("✅")
