import backoff
from src.library.api import HttpAPI, METHOD
from src.library.api.handler import JsonResponse, StreamResponse
from src.library.api.session import NoAuthorization, TokenAuthorization
from src.library.api.exceptions import *
from src.library.utils import getenv

PTERODACTYL_API_URL = getenv("PTERODACTYL_API_URL")
PTERODACTYL_TOKEN = getenv("PTERODACTYL_TOKEN", fail_on_none=False)

class PterodactylAPI(HttpAPI):
    def __init__(self) -> None:
        if PTERODACTYL_TOKEN is None:
            session_auth = NoAuthorization()
        else:
            session_auth = TokenAuthorization(
                token=PTERODACTYL_TOKEN)

        super().__init__(
            base_url=PTERODACTYL_API_URL,
            session_auth=session_auth,
            raise_for_status=True)
    
    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def permissions(self) -> dict:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'client/permissions')
        return handler.json().get("attributes", {})
    
    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def account(self) -> dict:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'client/account')
        return handler.json().get("attributes", {})
    
    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def server_list(self) -> list[dict]:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'client',
            query={
                "type": "admin-all"
            })
        return handler.json().get("data", [])
    
    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def server_id(self, server_id: str) -> dict:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'client/servers/{server_id}')
        return handler.json()
    
    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def server_command(self, server_id: str, command: str) -> dict:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'client/servers/{server_id}/command',
            query={"command": command})
        return handler.json()
    
    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def server_power(self, server_id: str, signal: str) -> dict:
        handler: JsonResponse = await self._request(
            method=METHOD.POST,
            path=f'client/servers/{server_id}/power',
            json={"signal": signal})
        return handler.json()
    
    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def server_files_list(self, server_id: str, directory: str) -> list[dict]:
        handler: JsonResponse = await self._request(
            method=METHOD.GET, path=f'client/servers/{server_id}/files/list',
            query={"directory": directory})
        return handler.json().get("data", [])
    
    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def server_files_download(self, server_id: str, filepath: str) -> dict:
        handler: JsonResponse = await self._request(
            method=METHOD.GET,
            path=f'client/servers/{server_id}/files/download',
            query={"file": filepath})
        return handler.json()
    
    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def server_files_upload(self, server_id: str, filepath: str, fileraw: bytes) -> dict:
        handler: JsonResponse = await self._request(
            method=METHOD.POST,
            path=f'client/servers/{server_id}/files/upload',
            query={"file": filepath},
            data=fileraw)
        return handler.json()
    
    @backoff.on_exception(backoff.expo, Exception, max_tries=2)
    async def server_files_delete(self, server_id: str, directory: str, files: list[str]) -> dict:
        handler: JsonResponse = await self._request(
            method=METHOD.POST,
            path=f'client/servers/{server_id}/files/upload',
            query={"root": directory, "files": files})
        return handler.json()
