import backoff
from aiohttp import FormData
from typing import Any, Generator, Optional
from src.library.api import HttpAPI, METHOD
from src.library.api.handler import JsonRequest, JsonResponse, MultiPartRequest, RequestHandler, StreamResponse
from src.library.api.session import NO_AUTHORIZE, SessionAuthorization, TokenAuthorization
from src.library.api.utils import backoff_on_rate_limit, not_rate_limited
from src.model.pterodactyl import PterodactylAccount, PterodactylBackup, PterodactylPermission, PterodactylFile, PterodactylServer, ServerResources, ServerState
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
    SERVICE = "Pterodactyl"

    def __init__(self) -> None:
        super().__init__(
            base_url=PTERODACTYL_API_URL)

        if PTERODACTYL_TOKEN:
            self.session_auth = TokenAuthorization(
                token=PTERODACTYL_TOKEN)

    @backoff_on_rate_limit
    @backoff.on_exception(backoff.expo, Exception, max_tries=2, giveup=not_rate_limited)
    async def account(self) -> PterodactylAccount:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'client/account')
        return PterodactylAccount(**handler.json.get("attributes", {}))

    @backoff_on_rate_limit
    @backoff.on_exception(backoff.expo, Exception, max_tries=2, giveup=not_rate_limited)
    async def permissions(self) -> Generator[PterodactylPermission, None, None]:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'client/permissions')
        return (PterodactylPermission(**item) for item in handler.json.get("attributes", {}).get("permissions", []))

    @backoff_on_rate_limit
    @backoff.on_exception(backoff.expo, Exception, max_tries=2, giveup=not_rate_limited)
    async def server_get(self, server_id: str) -> PterodactylServer:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'client/servers/{server_id}')
        return PterodactylServer(**handler.json.get("attributes", {}))

    @backoff_on_rate_limit
    @backoff.on_exception(backoff.expo, Exception, max_tries=2, giveup=not_rate_limited)
    async def server_get_many(self) -> Generator[PterodactylServer, None, None]:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'client',
            query={
                "type": "admin-all"
            })
        return (PterodactylServer(**item.get("attributes", {})) for item in handler.json.get("data", []))
    
    @backoff_on_rate_limit
    @backoff.on_exception(backoff.expo, Exception, max_tries=2, giveup=not_rate_limited)
    async def server_files(self, server_id: str, directory: str) -> Generator[PterodactylFile, None, None]:
        handler: JsonResponse = await self._request(
            method=METHOD.GET, path=f'client/servers/{server_id}/files/list',
            query={"directory": directory})
        return (PterodactylFile(**item.get("attributes", {})) for item in handler.json.get("data", []))

    @backoff_on_rate_limit
    @backoff.on_exception(backoff.expo, Exception, max_tries=2, giveup=not_rate_limited)
    async def server_files_signed_url(self, server_id: str, action: str, query: Optional[dict[str, str]] = None) -> str:
        """A one-use URL on the node (Wings), valid 15 minutes. Asking for one changes nothing."""
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'client/servers/{server_id}/files/{action}',
            query=query)
        url: str = handler.json["attributes"]["url"]
        return url

    @backoff_on_rate_limit
    async def server_files_download(self, server_id: str, filepath: str) -> bytes:
        url = await self.server_files_signed_url(server_id, "download", {"file": filepath})
        # The signed URL carries its own token and points at another host: never send the key.
        handler: StreamResponse = await self._request(
            method=METHOD.GET,
            path=url,
            session_auth=NO_AUTHORIZE,
            response=StreamResponse())
        return handler.stream

    @backoff_on_rate_limit
    @backoff.on_exception(backoff.expo, Exception, max_tries=2, giveup=not_rate_limited)
    async def server_state(self, server_id: str) -> ServerState:
        handler: JsonResponse = await self._request(method=METHOD.GET, path=f'client/servers/{server_id}')
        attributes = handler.json["attributes"]
        return ServerState(status=attributes.get("status"), is_suspended=attributes["is_suspended"],
                           is_installing=attributes["is_installing"], is_transferring=attributes["is_transferring"],
                           backup_limit=attributes["feature_limits"]["backups"])

    @backoff_on_rate_limit
    @backoff.on_exception(backoff.expo, Exception, max_tries=2, giveup=not_rate_limited)
    async def resources(self, server_id: str) -> ServerResources:
        handler: JsonResponse = await self._request(method=METHOD.GET, path=f'client/servers/{server_id}/resources')
        attributes = handler.json["attributes"]
        return ServerResources(current_state=attributes["current_state"], is_suspended=attributes["is_suspended"],
                               uptime_ms=attributes["resources"]["uptime"])

    @backoff_on_rate_limit
    @backoff.on_exception(backoff.expo, Exception, max_tries=2, giveup=not_rate_limited)
    async def backups(self, server_id: str) -> tuple[list[PterodactylBackup], int]:
        """The server's backups (first page, up to 50) and backup_count: the non-failed ones, which
        the panel holds against feature_limits.backups."""
        handler: JsonResponse = await self._request(method=METHOD.GET, path=f'client/servers/{server_id}/backups',
                                                    query={"per_page": "50"})
        backups = [PterodactylBackup(**item["attributes"]) for item in handler.json.get("data", [])]
        return backups, int(handler.json.get("meta", {}).get("backup_count", len(backups)))

    @backoff_on_rate_limit
    @backoff.on_exception(backoff.expo, Exception, max_tries=2, giveup=not_rate_limited)
    async def backup_get(self, server_id: str, backup_id: str) -> PterodactylBackup:
        handler: JsonResponse = await self._request(method=METHOD.GET, path=f'client/servers/{server_id}/backups/{backup_id}')
        return PterodactylBackup(**handler.json["attributes"])

    # Writes below are never retried (d-7985ee-05d032): a lost answer does not say whether the
    # server already did it. Routes and bodies per the panel's routes/api-client.php. Every one
    # goes through _write, the only place that sends a write (scripts/audit.py ungated_writes).
    @staticmethod
    def _require_writes() -> None:
        if not PTERODACTYL_WRITES:
            raise HTTP_403_FORBIDDEN("Pterodactyl is read-only here: set PTERODACTYL_WRITES to 'enabled' to write")

    async def _write(self, path: str, json: Optional[dict] = None, query: Optional[dict] = None,
                     body: Any = None, session_auth: Optional[SessionAuthorization] = None,
                     request: Optional[RequestHandler] = None, method: METHOD = METHOD.POST) -> JsonResponse:
        """Send a write (POST, or PUT for a rename) to the panel or a node, unless writes are off:
        then refuse first."""
        self._require_writes()
        handler: JsonResponse = await self._request(
            method=method, path=path, json=json, query=query, body=body,
            session_auth=session_auth, request=request or JsonRequest())
        return handler

    async def backup_create(self, server_id: str, name: str, locked: bool) -> PterodactylBackup:
        """Start a backup; it completes later (completed_at, is_successful). The panel refuses it
        when the server has no backup slot left, and throttles it with 429."""
        handler = await self._write(f'client/servers/{server_id}/backups', json={"name": name, "is_locked": locked})
        return PterodactylBackup(**handler.json["attributes"])

    async def backup_lock_toggle(self, server_id: str, backup_id: str) -> PterodactylBackup:
        handler = await self._write(f'client/servers/{server_id}/backups/{backup_id}/lock')
        return PterodactylBackup(**handler.json["attributes"])

    async def backup_restore(self, server_id: str, backup_id: str, truncate: bool) -> bool:
        """Restore a backup; the panel requires the server idle (status None) and the backup
        successful. With truncate, the server's files are deleted first: not atomic."""
        handler = await self._write(f'client/servers/{server_id}/backups/{backup_id}/restore', json={"truncate": truncate})
        return handler.status == 204

    async def files_rename(self, server_id: str, root: str, pairs: list[tuple[str, str]]) -> bool:
        handler = await self._write(f'client/servers/{server_id}/files/rename', method=METHOD.PUT,
                                    json={"root": root, "files": [{"from": a, "to": b} for a, b in pairs]})
        return handler.status == 204

    async def files_create_folder(self, server_id: str, root: str, name: str) -> bool:
        handler = await self._write(f'client/servers/{server_id}/files/create-folder', json={"root": root, "name": name})
        return handler.status == 204

    async def server_files_upload(self, server_id: str, directory: str, filename: str, fileraw: bytes) -> bool:
        return await self.server_files_upload_many(server_id, directory, [(filename, fileraw)])

    async def server_files_upload_many(self, server_id: str, directory: str, files: list[tuple[str, bytes]]) -> bool:
        """Upload several files in one POST to Wings, under one signed URL (U2): Wings reads every
        `files` part (its upload_limit is per file) and the token is single-use. Two requests for
        the whole batch instead of two per file."""
        self._require_writes()
        url = await self.server_files_signed_url(server_id, "upload")
        # quote_fields=False: aiohttp otherwise percent-encodes the file name, and Wings keeps it as
        # sent, so a jar named "x+mc26.3.jar" would land as "x%2Bmc26.3.jar".
        form = FormData(quote_fields=False)
        for filename, fileraw in files:
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
