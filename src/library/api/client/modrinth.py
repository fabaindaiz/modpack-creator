import backoff
from typing import Generator
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
    async def project_get_one(self, slug: str) -> ModrinthProject:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'project/{slug}')
        return ModrinthProject.parse(**handler.json)

    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def project_get_many(self, slugs: list[str]) -> Generator[ModrinthProject, None, None]:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path='project',
            query={"ids": str(slugs)})
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
    async def project_versions(self, slug: str, loaders: list[str] = [], game_versions: list[str] = [], featured: bool = True) -> Generator[ModrinthVersion, None, None]:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'project/{slug}/version',
            query={
                "loaders": str(loaders),
                "game_versions": str(game_versions),
                "featured": boolToStr(featured, int_format=False)
            })
        return (ModrinthVersion.parse(**item) for item in handler.json)
    
    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def version_get_one(self, id: str) -> ModrinthVersion:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'version/{id}')
        return ModrinthVersion.parse(**handler.json)
    
    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def version_get_many(self, ids: list[str]) -> Generator[ModrinthVersion, None, None]:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path='version',
            query={"ids": str(ids)})
        return (ModrinthVersion.parse(**item) for item in handler.json)

    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def files_hash_get_one(self, hash: str, algorithm: str = "sha512") -> ModrinthVersion:
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
    async def files_update_hash_post_one(self, hash: str, algorithm: str = "sha512", loaders: list[str] = [], game_versions: list[str] = []) -> ModrinthVersion:
        handler: JsonResponse = await self._request(
            method=METHOD.POST,
            path=f'version_file/{hash}/update',
            query={"algorithm": algorithm},
            json={
                "loaders": str(loaders),
                "game_versions": str(game_versions)
            })
        return ModrinthVersion.parse(**handler.json)

    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def files_update_hash_post_many(self, hashes: list[str], algorithm: str = "sha512", loaders: list[str] = [], game_versions: list[str] = []) -> ModrinthVersion:
        handler: JsonResponse = await self._request(
            method=METHOD.POST,
            path='version_file/update',
            json={
                "hashes": str(hashes),
                "algorithm": algorithm,
                "loaders": str(loaders),
                "game_versions": str(game_versions)
            })
        return ModrinthVersion.parse(**handler.json)