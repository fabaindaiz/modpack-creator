import backoff
from typing import Any, Optional
from src.library.api import HttpAPI, METHOD
from src.library.api.handler import JsonResponse
from src.library.mrpack import fabric_loader_version, quilt_loader_version
from src.library.utils import getenv

FABRIC_META_URL = getenv("FABRIC_META_URL", "https://meta.fabricmc.net/")
QUILT_META_URL = getenv("QUILT_META_URL", "https://meta.quiltmc.org/")

class LoaderMetaAPI(HttpAPI):
    """The loaders' own metadata, for the version a build freezes (research §2). Only reads.
    NeoForge and Forge have none here: their version is the admin's to set."""
    SERVICE = "el servicio de loaders"

    def __init__(self, fabric_url: str = FABRIC_META_URL, quilt_url: str = QUILT_META_URL) -> None:
        super().__init__(base_url=None)
        self.fabric_url = fabric_url
        self.quilt_url = quilt_url

    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def _get(self, url: str) -> Any:
        handler: JsonResponse = await self._request(method=METHOD.GET, path=url)
        return handler.json

    async def loader_version(self, loader: str, game_version: str) -> Optional[str]:
        """The loader version to use for this game version, or None if there is no meta for it."""
        if loader == "fabric":
            return fabric_loader_version(await self._get(f"{self.fabric_url}v2/versions/loader/{game_version}"))
        if loader == "quilt":
            return quilt_loader_version(await self._get(f"{self.quilt_url}v3/versions/loader/{game_version}"))
        return None
