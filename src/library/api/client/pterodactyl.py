import backoff
from aiohttp import FormData
from typing import Any, Generator, Optional
from src.library.api import HttpAPI, METHOD
from src.library.api.handler import JsonRequest, JsonResponse, MultiPartRequest, RequestHandler, StreamResponse
from src.library.api.session import NO_AUTHORIZE, SessionAuthorization, TokenAuthorization
from src.model.pterodactyl import PterodactylAccount, PterodactylPermission, PterodactylFile, PterodactylServer
from src.library.utils import getenv, getenv_opt
from src.exceptions import *

PTERODACTYL_API_URL = getenv("PTERODACTYL_API_URL")
PTERODACTYL_TOKEN = getenv_opt("PTERODACTYL_TOKEN")

def writes_enabled(value: Optional[str]) -> bool:
    """Writes to Pterodactyl happen only when PTERODACTYL_WRITES is exactly "enabled"."""
    return (value or "").strip().lower() == "enabled"

# The process-wide switch (one-write-gate-makes-read-only-real): off unless configured, and no
# caller can turn it on. Reading servers never needs it.
PTERODACTYL_WRITES = writes_enabled(getenv_opt("PTERODACTYL_WRITES"))

class PterodactylAPI(HttpAPI):
    def __init__(self) -> None:
        super().__init__(
            base_url=PTERODACTYL_API_URL)

        if PTERODACTYL_TOKEN:
            self.session_auth = TokenAuthorization(
                token=PTERODACTYL_TOKEN)

    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def account(self) -> PterodactylAccount:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'client/account')
        return PterodactylAccount(**handler.json.get("attributes", {}))

    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def permissions(self) -> Generator[PterodactylPermission, None, None]:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'client/permissions')
        return (PterodactylPermission(**item) for item in handler.json.get("attributes", {}).get("permissions", []))

    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def server_get(self, server_id: str) -> PterodactylServer:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'client/servers/{server_id}')
        return PterodactylServer(**handler.json.get("attributes", {}))

    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def server_get_many(self) -> Generator[PterodactylServer, None, None]:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'client',
            query={
                "type": "admin-all"
            })
        return (PterodactylServer(**item.get("attributes", {})) for item in handler.json.get("data", []))
    
    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def server_files(self, server_id: str, directory: str) -> Generator[PterodactylFile, None, None]:
        handler: JsonResponse = await self._request(
            method=METHOD.GET, path=f'client/servers/{server_id}/files/list',
            query={"directory": directory})
        return (PterodactylFile(**item.get("attributes", {})) for item in handler.json.get("data", []))

    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def server_files_signed_url(self, server_id: str, action: str, query: Optional[dict[str, str]] = None) -> str:
        """A one-use URL on the node (Wings), valid 15 minutes. Asking for one changes nothing."""
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'client/servers/{server_id}/files/{action}',
            query=query)
        url: str = handler.json["attributes"]["url"]
        return url

    async def server_files_download(self, server_id: str, filepath: str) -> bytes:
        url = await self.server_files_signed_url(server_id, "download", {"file": filepath})
        # The signed URL carries its own token and points at another host: never send the key.
        handler: StreamResponse = await self._request(
            method=METHOD.GET,
            path=url,
            session_auth=NO_AUTHORIZE,
            response=StreamResponse())
        return handler.stream

    # Writes below are never retried (d-7985ee-05d032): a lost answer does not say whether the
    # server already did it. Routes and bodies per the panel's routes/api-client.php. Every one
    # goes through _write, the only place that sends a write (scripts/audit.py ungated_writes).
    @staticmethod
    def _require_writes() -> None:
        if not PTERODACTYL_WRITES:
            raise HTTP_403_FORBIDDEN("Pterodactyl is read-only here: set PTERODACTYL_WRITES to 'enabled' to write")

    async def _write(self, path: str, json: Optional[dict] = None, query: Optional[dict] = None,
                     body: Any = None, session_auth: Optional[SessionAuthorization] = None,
                     request: Optional[RequestHandler] = None) -> JsonResponse:
        """Send a write (a POST) to the panel or a node, unless writes are off: then refuse first."""
        self._require_writes()
        handler: JsonResponse = await self._request(
            method=METHOD.POST, path=path, json=json, query=query, body=body,
            session_auth=session_auth, request=request or JsonRequest())
        return handler

    async def server_files_upload(self, server_id: str, directory: str, filename: str, fileraw: bytes) -> bool:
        self._require_writes()
        url = await self.server_files_signed_url(server_id, "upload")
        form = FormData()
        form.add_field("files", fileraw, filename=filename, content_type="application/octet-stream")
        handler = await self._write(url, query={"directory": directory}, body=form,
                                    session_auth=NO_AUTHORIZE, request=MultiPartRequest())
        return handler.status == 200

    async def server_files_delete(self, server_id: str, directory: str, files: list[str]) -> bool:
        handler = await self._write(f'client/servers/{server_id}/files/delete', json={"root": directory, "files": files})
        return handler.status == 204

    async def server_command(self, server_id: str, command: str) -> bool:
        handler = await self._write(f'client/servers/{server_id}/command', json={"command": command})
        return handler.status == 204

    async def server_power(self, server_id: str, signal: str) -> bool:
        handler = await self._write(f'client/servers/{server_id}/power', json={"signal": signal})
        return handler.status == 204
