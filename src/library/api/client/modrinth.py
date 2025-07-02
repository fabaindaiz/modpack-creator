import backoff
from src.library.api import HttpAPI, METHOD
from src.library.api.handler import JsonResponse, StreamResponse
from src.library.api.session import NoAuthorization, TokenAuthorization
from src.library.api.exceptions import *
from src.library.utils import getenv, boolToStr

MODRINTH_API_URL = getenv("MODRINTH_API_URL", "https://api.modrinth.com/v2/")
MODRINTH_TOKEN = getenv("MODRINTH_TOKEN", fail_on_none=False)
MODRINTH_AGENT = getenv("MODRINTH_AGENT", fail_on_none=False)

class ModrinthCDN(HttpAPI):
    streamResponse = StreamResponse()

    def __init__(self) -> None:
        if MODRINTH_TOKEN is None:
            session_auth = NoAuthorization()
        else:
            session_auth = TokenAuthorization(
                token=MODRINTH_TOKEN,
                scheme="apiKey ")

        super().__init__(
            base_url=None,
            session_auth=session_auth,
            raise_for_status=True)
    
    def headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if MODRINTH_AGENT:
            headers["User-Agent"] = MODRINTH_AGENT
        return headers
    
    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def download_file(self, url: str) -> bytes:
        handler: StreamResponse = await self._request(
            method=METHOD.GET,
            path=url,
            headers=self.headers(),
            response=self.streamResponse)
        return handler.stream()

class ModrinthAPI(HttpAPI):
    def __init__(self) -> None:
        if MODRINTH_TOKEN is None:
            session_auth = NoAuthorization()
        else:
            session_auth = TokenAuthorization(
                token=MODRINTH_TOKEN,
                scheme="apiKey ")

        super().__init__(
            base_url=MODRINTH_API_URL,
            session_auth=session_auth,
            raise_for_status=True)
    
    def headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if MODRINTH_AGENT:
            headers["User-Agent"] = MODRINTH_AGENT
        return headers

    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def project_info(self, slug: str) -> dict:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'project/{slug}',
            headers=self.headers())
        return handler.json()
    
    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def project_dependencies(self, slug: str) -> dict:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'project/{slug}/dependencies',
            headers=self.headers())
        return handler.json()
    
    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def project_versions(self, slug: str, loaders: list[str] = [], game_versions: list[str] = [], featured: bool = True) -> dict:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'project/{slug}/version',
            query={
                "loaders": str(loaders),
                "game_versions": str(game_versions),
                "featured": boolToStr(featured, int_format=False)
            },
            headers=self.headers())
        return handler.json()