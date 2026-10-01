from src.library.api import HttpAPI, METHOD
from src.library.api.handler import JsonResponse

# Discord's limit for a message's content.
CONTENT_MAX = 2000

class DiscordWebhookAPI(HttpAPI):
    """Execute Webhook (docs.discord.com/developers/resources/webhook): POST to the webhook's URL,
    answered 204 without `wait`. The URL carries its token: it comes from configuration, and is
    never stored with a job or logged."""
    def __init__(self) -> None:
        super().__init__(base_url=None)

    async def send(self, webhook_url: str, text: str) -> None:
        """Post a message. Mentions are never parsed: a text with @everyone pings nobody. Not
        retried here: the job that sends it retries, waiting what a 429 asks."""
        handler: JsonResponse = await self._request(
            method=METHOD.POST,
            path=webhook_url,
            json={"content": text[:CONTENT_MAX], "allowed_mentions": {"parse": []}})
        if handler.status not in (200, 204):
            raise RuntimeError(f"Discord answered {handler.status}")
