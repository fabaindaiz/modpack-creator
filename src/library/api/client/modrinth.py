import backoff
import json
from typing import Generator, Sequence
from src.library.api import HttpAPI, METHOD
from src.library.api.handler import JsonResponse
from src.library.api.session import TokenAuthorization
from src.model.modrinth import ModrinthProject, ModrinthVersion
from src.library.utils import getenv, getenv_opt, boolToStr

MODRINTH_API_URL = getenv("MODRINTH_API_URL", "https://api.modrinth.com/v2/")
MODRINTH_TOKEN = getenv_opt("MODRINTH_TOKEN")
MODRINTH_AGENT = getenv_opt("MODRINTH_AGENT")

class ModrinthAPI(HttpAPI):
    def __init__(self) -> None:
        super().__init__(
            base_url=MODRINTH_API_URL)
        
        if MODRINTH_TOKEN:
            self.session_auth = TokenAuthorization(
                token=MODRINTH_TOKEN,
                scheme="apiKey ")
        if MODRINTH_AGENT:
            self.session_headers = {
                "User-Agent": MODRINTH_AGENT}

    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def project_get(self, slug: str) -> ModrinthProject:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'project/{slug}')
        return ModrinthProject.parse(**handler.json)

    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def project_get_many(self, slugs: list[str]) -> Generator[ModrinthProject, None, None]:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path='projects',
            query={"ids": json.dumps(slugs)})
        return (ModrinthProject.parse(**item) for item in handler.json)
    
    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def project_dependencies(self, slug: str) -> tuple[Generator[ModrinthProject, None, None], Generator[ModrinthVersion, None, None]]:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'project/{slug}/dependencies')
        projects = (ModrinthProject.parse(**item) for item in handler.json.get("projects", []))
        versions = (ModrinthVersion.parse(**item) for item in handler.json.get("versions", []))
        return projects, versions

    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def project_versions(self, slug: str, loaders: Sequence[str] = (), game_versions: Sequence[str] = (), featured: bool = True) -> Generator[ModrinthVersion, None, None]:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'project/{slug}/version',
            query={
                "loaders": json.dumps(loaders),
                "game_versions": json.dumps(game_versions),
                "featured": boolToStr(featured, int_format=False)
            })
        return (ModrinthVersion.parse(**item) for item in handler.json)
    
    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def version_get(self, id: str) -> ModrinthVersion:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'version/{id}')
        return ModrinthVersion.parse(**handler.json)
    
    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def version_get_many(self, ids: list[str]) -> Generator[ModrinthVersion, None, None]:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path='versions',
            query={"ids": json.dumps(ids)})
        return (ModrinthVersion.parse(**item) for item in handler.json)

    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def files_hash_get(self, hash: str, algorithm: str = "sha512") -> ModrinthVersion:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'version_file/{hash}',
            query={"algorithm": algorithm})
        return ModrinthVersion.parse(**handler.json)

    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def files_hash_post_many(self, hashes: list[str], algorithm: str = "sha512") -> Generator[ModrinthVersion, None, None]:
        handler: JsonResponse = await self._request(
            method=METHOD.POST,
            path='version_file',
            json={
                "hashes": str(hashes),
                "algorithm": algorithm
            })
        return (ModrinthVersion.parse(**item) for item in handler.json)

    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def files_update_hash_post(self, hash: str, algorithm: str = "sha512", loaders: Sequence[str] = (), game_versions: Sequence[str] = ()) -> ModrinthVersion:
        handler: JsonResponse = await self._request(
            method=METHOD.POST,
            path=f'version_file/{hash}/update',
            query={"algorithm": algorithm},
            json={
                "loaders": json.dumps(loaders),
                "game_versions": json.dumps(game_versions)
            })
        return ModrinthVersion.parse(**handler.json)

    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def files_update_hash_post_many(self, hashes: list[str], algorithm: str = "sha512", loaders: Sequence[str] = (), game_versions: Sequence[str] = ()) -> ModrinthVersion:
        handler: JsonResponse = await self._request(
            method=METHOD.POST,
            path='version_file/update',
            json={
                "hashes": json.dumps(hashes),
                "algorithm": algorithm,
                "loaders": json.dumps(loaders),
                "game_versions": json.dumps(game_versions)
            })
        return ModrinthVersion.parse(**handler.json)