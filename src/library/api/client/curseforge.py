"""CurseForge's API (T5): only reads, and only with the owner's own key (C1; `CURSEFORGE_API_KEY`
in `.env.yaml`, never versioned). Without a key the client is off and nothing calls it.

`POST /v1/mods/files` is CurseForge's documented Core API (docs.curseforge.com/rest-api). The
shared-profile download is **not** documented (C5, owner's decision against the recommendation):
it is kept apart here, one method, so its failing never touches the rest. Nothing CurseForge sends
is stored: a file id becomes a sha1 that Modrinth is asked about.
"""
from typing import Any, Optional

import backoff

from src.library.api import METHOD, HttpAPI
from src.library.api.handler import JsonResponse
from src.library.api.handler.response import CappedStreamResponse
from src.library.api.utils import not_retryable
from src.library.utils import getenv

CURSEFORGE_API_URL = getenv("CURSEFORGE_API_URL", "https://api.curseforge.com/")
SHA1 = 1  # HashAlgo: 1 = Sha1, 2 = Md5


class CurseForgeAPI(HttpAPI):
    SERVICE = "CurseForge"

    def __init__(self, api_key: Optional[str], base_url: str = CURSEFORGE_API_URL) -> None:
        super().__init__(base_url=base_url)
        self.enabled = bool(api_key)
        if api_key:
            self.session_headers = {"x-api-key": api_key}

    @backoff.on_exception(backoff.expo, Exception, max_tries=2, giveup=not_retryable)
    async def files_get_many(self, file_ids: list[int]) -> dict[int, str]:
        """File id → its sha1, lowercase, in one request; a file without one, or not returned, is absent."""
        handler: JsonResponse = await self._request(method=METHOD.POST, path="v1/mods/files",
                                                     json={"fileIds": list(file_ids)})
        found: dict[int, str] = {}
        for item in handler.json.get("data", []) if isinstance(handler.json, dict) else []:
            sha1 = next((h.get("value") for h in item.get("hashes", []) if isinstance(h, dict) and h.get("algo") == SHA1), None)
            if isinstance(item.get("id"), int) and isinstance(sha1, str) and sha1:
                found[item["id"]] = sha1.lower()
        return found

    async def shared_profile(self, code: str, max_bytes: int) -> bytes:
        """The zip of a CurseForge profile code (undocumented endpoint, C5), at most `max_bytes`:
        TooLarge past it. One attempt: a code lives 7 days and asking again changes nothing."""
        handler = await self._request(method=METHOD.GET, path=f"v1/shared-profile/{code}",
                                      response=CappedStreamResponse(max_bytes))
        data: bytes = handler.stream
        return data
