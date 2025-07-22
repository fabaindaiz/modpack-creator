import backoff
from typing import Generator
from src.library.api import HttpAPI, METHOD
from src.library.api.handler import JsonResponse, StreamResponse
from src.library.api.session import TokenAuthorization
from src.model.pterodactyl import PterodactylAccount, PterodactylPermission, PterodactylFile, PterodactylServer
from src.library.utils import getenv, getenv_opt
from src.exceptions import *

PTERODACTYL_API_URL = getenv("PTERODACTYL_API_URL")
PTERODACTYL_TOKEN = getenv_opt("PTERODACTYL_TOKEN")

class PterodactylAPI(HttpAPI):
    streamResponse = StreamResponse()

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
    async def server_get_one(self, server_id: str) -> PterodactylServer:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'client/servers/{server_id}')
        return PterodactylServer(**handler.json)

    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def server_get_many(self) -> Generator[PterodactylServer, None, None]:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'client',
            query={
                "type": "admin-all"
            })
        return (PterodactylServer(**item) for item in handler.json.get("data", []))
    
    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def server_files(self, server_id: str, directory: str) -> Generator[PterodactylFile, None, None]:
        handler: JsonResponse = await self._request(
            method=METHOD.GET, path=f'client/servers/{server_id}/files/list',
            query={"directory": directory})
        return (PterodactylFile(**item) for item in handler.json.get("data", []))

    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def server_files_download(self, server_id: str, filepath: str) -> bytes:
        handler: StreamResponse = await self._request(
            method=METHOD.GET,
            path=f'client/servers/{server_id}/files/download',
            query={"file": filepath},
            response=self.streamResponse)
        return handler.stream

    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def server_files_upload(self, server_id: str, filepath: str, fileraw: bytes) -> bool:
        handler: JsonResponse = await self._request(
            method=METHOD.POST,
            path=f'client/servers/{server_id}/files/upload',
            query={"file": filepath},
            data=fileraw)
        return handler.status == 200
    
    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def server_files_delete(self, server_id: str, directory: str, files: list[str]) -> bool:
        handler: JsonResponse = await self._request(
            method=METHOD.POST,
            path=f'client/servers/{server_id}/files/delete',
            query={"root": directory, "files": files})
        return handler.status == 204
    
    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def server_command(self, server_id: str, command: str) -> bool:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'client/servers/{server_id}/command',
            query={"command": command})
        return handler.status == 204
    
    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def server_power(self, server_id: str, signal: str) -> bool:
        handler: JsonResponse = await self._request(
            method=METHOD.POST,
            path=f'client/servers/{server_id}/power',
            json={"signal": signal})
        return handler.status == 204