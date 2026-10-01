from beanie import PydanticObjectId
from beanie.operators import In
from beanie.odm.documents import FindMany
from typing import Iterable, Optional
from src.model.pterodactyl import (
    PterodactylServer,
    PterodactylVersion,
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
    
    def version_find(self,
            version_ids: Optional[list[str]] = None,
            server_id: Optional[str] = None,
            ) -> FindMany[PterodactylVersion]:
        """
        Get all Pterodactyl versions.
        
        :param version_ids: Optional list of version IDs to filter by.
        :param server_id: Optional server ID to filter versions by.
        :return: An iterable of PterodactylVersion objects.
        """
        query = PterodactylVersion.find()
        if version_ids:
            query = query.find(In(PterodactylVersion.id, (PydanticObjectId(id) for id in version_ids)))
        if server_id:
            query = query.find(PterodactylVersion.server.id == PydanticObjectId(server_id)) # type: ignore
        return query
    
    async def version_get(self, version_id: str) -> PterodactylVersion:
        """
        Get a single Pterodactyl version by its ID.
        
        :param id: The ID of the version to retrieve.
        :return: The PterodactylVersion object.
        """
        document = await PterodactylVersion.get(version_id)
        if not document:
            raise HTTP_404_NOT_FOUND(f"Version with ID '{version_id}' not found.")
        return document
    
    async def version_insert(self, version: PterodactylVersion) -> PterodactylVersion:
        """
        Add a single Pterodactyl version to the database.
        
        :param version: The PterodactylVersion object to add.
        :return: The created PterodactylVersion object.
        """
        return await PterodactylVersion.create(version)
    
    async def version_insert_many(self, versions: Iterable[PterodactylVersion]) -> Iterable[PterodactylVersion]:
        """
        Add multiple Pterodactyl versions to the database.
        
        :param versions: The list of PterodactylVersion objects to add.
        """
        result = await PterodactylVersion.insert_many(versions)
        return await self.version_find(version_ids=result.inserted_ids).to_list()

    async def version_save(self, version: PterodactylVersion, version_id: Optional[PydanticObjectId] = None) -> PterodactylVersion:
        """
        Add a version to the database.
        
        :param version: The PterodactylVersion object to add.
        :return: The created PterodactylVersion object.
        """
        if version_id:
            version.id = PydanticObjectId(version_id)
        await version.save()
        return version