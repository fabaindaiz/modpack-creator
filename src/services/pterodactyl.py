from src.model.pterodactyl.server import PterodactylFile, PterodactylServer
from src.library.api.client.pterodactyl import PterodactylAPI
from src.database.pterodactyl import PterodactylDatabase

class PterodactylService():
    def __init__(self,
            pterodactylAPI: PterodactylAPI,
            PterodactylDB: PterodactylDatabase,
            ) -> None:
        self.pterodactylAPI = pterodactylAPI
        self.pterodactylDB = PterodactylDB

    async def server_create(self, uuid: str) -> PterodactylServer:
        """
        Create a new Pterodactyl server.
        
        :param uuid: The UUID of the server to create.
        :return: The created PterodactylServer object.
        """
        server = await self.pterodactylAPI.server_get(uuid)
        return await self.pterodactylDB.server_insert(server)
    
    async def server_update(self, server_id: str) -> PterodactylServer:
        """
        Update a Pterodactyl server.
        
        :param server_id: The ID of the server to update.
        :return: The updated PterodactylServer object.
        """
        document = await self.pterodactylDB.server_get(server_id)
        server = await self.pterodactylAPI.server_get(document.uuid)
        return await self.pterodactylDB.server_save(document, server.id)
    
    async def server_files(self, server_id: str, directory: str) -> list[PterodactylFile]:
        """
        Get the files of a Pterodactyl server.
        
        :param server_id: The ID of the server to get files from.
        :return: A list of PterodactylFile objects.
        """
        server = await self.pterodactylDB.server_get(server_id)
        return await self.pterodactylAPI.server_files(server.identifier, directory)