from beanie import PydanticObjectId
from beanie.operators import In
from beanie.odm.documents import FindMany
from typing import Iterable, Optional
from src.model.pterodactyl import (
    PterodactylServer,
)
from src.exceptions import *

class PterodactylDatabase():
    def server_find(self,
            server_ids: Optional[list[str]] = None,
            ) -> FindMany[PterodactylServer]:
        """
        Get all Pterodactyl servers.
        
        :return: An iterable of PterodactylServer objects.
        """
        query = PterodactylServer.find()
        if server_ids:
            query = query.find(In(PterodactylServer.id, (PydanticObjectId(id) for id in server_ids)))
        return query

    async def server_get(self, server_id: str) -> PterodactylServer:
        """
        Get a single Pterodactyl server by its ID.
        
        :param id: The ID of the server to retrieve.
        :return: The PterodactylServer object.
        """
        document = await PterodactylServer.get(server_id)
        if not document:
            raise ValueError(f"Server with id '{server_id}' not found.")
        return document
    
    async def server_insert(self, server: PterodactylServer) -> PterodactylServer:
        """
        Create a new Pterodactyl server.
        
        :param server: The PterodactylServer object to create.
        :return: The created PterodactylServer object.
        """
        return await PterodactylServer.create(server)
    
    async def server_insert_many(self, servers: Iterable[PterodactylServer]) -> Iterable[PterodactylServer]:
        """
        Add multiple Pterodactyl servers to the database.
        
        :param servers: The list of PterodactylServer objects to add.
        """
        result = await PterodactylServer.insert_many(servers)
        return await self.server_find(server_ids=result.inserted_ids).to_list()
    
    async def server_save(self, server: PterodactylServer, server_id: Optional[PydanticObjectId] = None) -> PterodactylServer:
        """
        Save a Pterodactyl server to the database.
        
        :param server: The PterodactylServer object to save.
        :param server_id: Optional ID to set for the server.
        :return: The saved PterodactylServer object.
        """
        if server_id:
            server.id = PydanticObjectId(server_id)
        await server.save()
        return server