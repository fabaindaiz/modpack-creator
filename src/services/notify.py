import logging
from datetime import timedelta
from typing import Any, Optional
from src.database.jobs import JobDatabase
from src.exceptions import HTTP_429_TOO_MANY_REQUESTS
from src.library.api.client.discord import DiscordWebhookAPI
from src.library.scan import drift_message  # noqa: F401  (re-exported for the notifications)
from src.library.utils import getenv_opt
from src.model.jobs import Job
from src.services.jobs import Handler, JobContext, RetryLater

DISCORD_WEBHOOK_URL = getenv_opt("DISCORD_WEBHOOK_URL")
logger = logging.getLogger("modpack.notify")

class Notifier():
    """Notifications by Discord webhook (i-7985ee-34933b), delivered as jobs: they survive Discord
    being down for a while, wait what a 429 asks, and are not sent twice once sent. Without a
    webhook configured, nothing is queued. A notification never fails what it reports on
    (best-effort-side-channels): a failure to queue it is logged and swallowed."""
    def __init__(self,
            jobDB: JobDatabase,
            api: Optional[DiscordWebhookAPI] = None,
            webhook_url: Optional[str] = DISCORD_WEBHOOK_URL,
            ) -> None:
        self.jobDB = jobDB
        self.api = api or DiscordWebhookAPI()
        self.webhook_url = webhook_url

    async def notify(self, text: str) -> None:
        if not self.webhook_url:
            return
        try:
            await self.jobDB.enqueue("notify", key=None, payload={"text": text}, max_attempts=10)
        except Exception:
            logger.warning("notification not queued", exc_info=True, extra={"text": text[:200]})

    def handlers(self) -> dict[str, Handler]:
        return {"notify": self._send}

    async def _send(self, job: Job, context: JobContext) -> None:
        async def send() -> dict[str, Any]:
            if not self.webhook_url:
                return {"sent": False}
            try:
                await self.api.send(self.webhook_url, job.payload["text"])
            except HTTP_429_TOO_MANY_REQUESTS as error:
                raise RetryLater(timedelta(seconds=error.retry_after or 30), "Discord rate limit") from error
            return {"sent": True}
        await context.step("send", send)

    async def close(self) -> None:
        await self.api.close()
