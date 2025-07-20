import backoff
from src.library.api import HttpAPI, METHOD
from src.library.api.handler import StreamResponse

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