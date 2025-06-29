import backoff
from src.library.dependency.core import Dependent, dependent
from src.library.api import HttpAPI, METHOD
from src.library.api.handler import JsonResponse, StreamResponse
from src.library.api.session import NoAuthSession, TokenSession
from src.library.api.exceptions import *
from src.library.utils.singleton import singleton
from src.library.utils import getenv, boolToStr

MODRINTH_API_URL = getenv("MODRINTH_API_URL", "https://api.modrinth.com/v2/")
MODRINTH_TOKEN = getenv("MODRINTH_TOKEN", fail_on_none=False)
MODRINTH_AGENT = getenv("MODRINTH_AGENT", fail_on_none=False)

@singleton
@dependent()
class ModrinthCDN(HttpAPI, Dependent):
    streamResponse = StreamResponse()

    def __init__(self) -> None:
        if MODRINTH_TOKEN is None:
            session_auth = NoAuthSession()
        else:
            session_auth = TokenSession(
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

@singleton
@dependent()
class ModrinthAPI(HttpAPI, Dependent):
    def __init__(self) -> None:
        if MODRINTH_TOKEN is None:
            session_auth = NoAuthSession()
        else:
            session_auth = TokenSession(
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
    async def project_get_one(self, slug: str) -> dict:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'project/{slug}',
            headers=self.headers())
        return handler.json()
    
    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def project_get_many(self, slugs: list[str]) -> dict:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path='project',
            query={
                "ids": str(slugs),
            },
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
    
    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def version_get_one(self, id: str) -> dict:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'version/{id}',
            headers=self.headers())
        return handler.json()
    
    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def version_get_many(self, ids: list[str]) -> dict:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path='version',
            query={
                "ids": str(ids),
            },
            headers=self.headers())
        return handler.json()

    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def files_hash_get_one(self, hash: str, algorithm: str = "sha512") -> dict:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'version_file/{hash}',
            query={
                "algorithm": algorithm,
            },
            headers=self.headers())
        return handler.json()
    
    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def files_update_hash_post_one(self, hash: str, algorithm: str = "sha512", loaders: list[str] = [], game_versions: list[str] = []) -> dict:
        handler: JsonResponse = await self._request(
            method=METHOD.POST,
            path=f'version_file/{hash}/update',
            query={
                "algorithm": algorithm,
            },
            json={
                "loaders": str(loaders),
                "game_versions": str(game_versions),
            },
            headers=self.headers())
        return handler.json()
    
    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def files_hash_post_many(self, hashes: list[str], algorithm: str = "sha512") -> dict:
        handler: JsonResponse = await self._request(
            method=METHOD.POST,
            path='version_file',
            json={
                "hashes": str(hashes),
                "algorithm": algorithm,
            },
            headers=self.headers())
        return handler.json()
    
    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def files_update_hash_post_many(self, hashes: list[str], algorithm: str = "sha512", loaders: list[str] = [], game_versions: list[str] = []) -> dict:
        handler: JsonResponse = await self._request(
            method=METHOD.POST,
            path='version_file/update',
            json={
                "hashes": str(hashes),
                "algorithm": algorithm,
                "loaders": str(loaders),
                "game_versions": str(game_versions),
            },
            headers=self.headers())
        return handler.json()
