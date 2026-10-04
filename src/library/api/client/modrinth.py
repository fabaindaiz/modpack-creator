import backoff
import json
from typing import Any, Generator, Optional, Sequence
from src.library.api import HttpAPI, METHOD
from src.library.api.handler import JsonResponse
from src.library.api.session import TokenAuthorization
from src.model.modrinth import ModrinthGameVersion, ModrinthProject, ModrinthVersion
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
    async def search(self, query: str, facets: list[list[str]], limit: int = 5) -> list[dict[str, Any]]:
        """GET /search: the hits for a text, filtered by facets (a JSON array of OR-groups, ANDed),
        as Modrinth returns them (openapi.yaml v2.7.0, SearchResults)."""
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path='search',
            query={"query": query, "facets": json.dumps(facets), "limit": str(limit)})
        hits: list[dict[str, Any]] = handler.json.get("hits", [])
        return hits

    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def game_versions(self) -> list[ModrinthGameVersion]:
        """Every game version Modrinth knows, newest first: the order to compare them by."""
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path='tag/game_version')
        return [ModrinthGameVersion(**item) for item in handler.json]

    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def project_versions(self, slug: str, loaders: Sequence[str] = (), game_versions: Sequence[str] = (), featured: Optional[bool] = None) -> Generator[ModrinthVersion, None, None]:
        # `featured` filters to featured-only or non-featured-only: omitted, every version comes.
        # The spec recommends include_changelog=false unless the changelog is used.
        query = {
            "loaders": json.dumps(list(loaders)),
            "game_versions": json.dumps(list(game_versions)),
            "include_changelog": "false",
        }
        if featured is not None:
            query["featured"] = boolToStr(featured, int_format=False)
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'project/{slug}/version',
            query=query)
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

    # The three POSTs below are lookups: Modrinth only reads, so retrying them is safe
    # (IDEMPOTENT_POSTS in scripts/audit.py). Bodies and answers per openapi.yaml v2.7.0.
    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def files_hash_post_many(self, hashes: list[str], algorithm: str = "sha512") -> dict[str, ModrinthVersion]:
        handler: JsonResponse = await self._request(
            method=METHOD.POST,
            path='version_files',
            json={
                "hashes": hashes,
                "algorithm": algorithm
            })
        return {hash: ModrinthVersion.parse(**item) for hash, item in handler.json.items()}

    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def files_update_hash_post(self, hash: str, algorithm: str = "sha512", loaders: Sequence[str] = (), game_versions: Sequence[str] = ()) -> ModrinthVersion:
        handler: JsonResponse = await self._request(
            method=METHOD.POST,
            path=f'version_file/{hash}/update',
            query={"algorithm": algorithm},
            json={
                "loaders": list(loaders),
                "game_versions": list(game_versions)
            })
        return ModrinthVersion.parse(**handler.json)

    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def files_update_hash_post_many(self, hashes: list[str], algorithm: str = "sha512", loaders: Sequence[str] = (), game_versions: Sequence[str] = (), version_types: Sequence[str] = ()) -> dict[str, ModrinthVersion]:
        # `version_types` (release, beta, alpha) is optional in openapi v2.7.0: sent only if given.
        body: dict[str, Any] = {
            "hashes": hashes,
            "algorithm": algorithm,
            "loaders": list(loaders),
            "game_versions": list(game_versions)
        }
        if version_types:
            body["version_types"] = list(version_types)
        handler: JsonResponse = await self._request(
            method=METHOD.POST,
            path='version_files/update',
            json=body)
        return {hash: ModrinthVersion.parse(**item) for hash, item in handler.json.items()}
