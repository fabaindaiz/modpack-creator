import backoff
from src.library.api import HttpAPI, METHOD
from src.library.api.handler import StreamResponse
from src.library.api.handler.response import CappedStreamResponse, TooLarge

__all__ = ["Downloader", "TooLarge"]

class Downloader(HttpAPI):
    streamResponse = StreamResponse()

    def __init__(self) -> None:
        super().__init__(
            base_url=None)
    
    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def download_file(self, url: str) -> bytes:
        handler: StreamResponse = await self._request(
            method=METHOD.GET,
            path=url,
            response=self.streamResponse)
        return handler.stream

    async def download_capped(self, url: str, max_bytes: int) -> bytes:
        """The file at `url`, at most `max_bytes`: TooLarge past it, without reading the rest. One
        attempt: a caller that wants another asks again (the owner counts every request)."""
        handler = await self._request(method=METHOD.GET, path=url, response=CappedStreamResponse(max_bytes))
        return handler.stream
