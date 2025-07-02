from src.model.pterodactyl import FileObject, PteroServer
from src.library.api.client.pterodactyl import PterodactylAPI

class ServerService():
    def __init__(self,
            pterodactylAPI: PterodactylAPI):
        self.__pterodactylAPI = pterodactylAPI
    
    @staticmethod
    def parse_server(data: dict):
        if data.get("object", None) != "server":
            raise ValueError("object is not a server")
        return PteroServer(**data["attributes"])

    @staticmethod
    def parse_file(data: dict):
        if data.get("object", None) != "file_object":
            raise ValueError("object is not a file_object")
        return FileObject(**data["attributes"])

    async def server_get(self) -> list[PteroServer]:
        server_list: list[dict] = await self.__pterodactylAPI.server_list()
        return [self.parse_server(data) for data in server_list]
    
    async def server_get_id(self, id: str) -> PteroServer:
        server: dict = await self.__pterodactylAPI.server_id(id)
        return self.parse_server(server)
    
    async def server_files_id(self, id: str, directory: str) -> list[FileObject]:
        file_list: list[dict] = await self.__pterodactylAPI.server_files_list(id, directory)
        return [self.parse_file(data) for data in file_list]
