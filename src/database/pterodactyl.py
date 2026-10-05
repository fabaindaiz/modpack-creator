from beanie import PydanticObjectId, SortDirection
from beanie.operators import In
from beanie.odm.documents import FindMany
from datetime import datetime
from typing import Iterable, Optional
from src.model.pterodactyl import (
    PterodactylServer,
    ServerScan,
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
        if not PydanticObjectId.is_valid(server_id):
            raise HTTP_404_NOT_FOUND(f"Server '{server_id}' not found")
        document = await PterodactylServer.get(server_id)
        if not document:
            raise HTTP_404_NOT_FOUND(f"Server '{server_id}' not found")
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
    
    async def server_set_bundle(self, server_id: str, bundle_id: Optional[str]) -> PterodactylServer:
        """Link a server to a bundle, or unlink it with None, in one atomic update."""
        result = await PterodactylServer.get_pymongo_collection().update_one(
            {"_id": self._server_oid(server_id)}, {"$set": {"bundle_id": bundle_id}})
        if not result.matched_count:
            raise HTTP_404_NOT_FOUND(f"Server '{server_id}' not found")
        return await self.server_get(server_id)

    async def scan_insert(self, scan: ServerScan) -> ServerScan:
        return await ServerScan.insert(scan)

    async def scan_find(self, server_id: str, directory: Optional[str] = None) -> list[ServerScan]:
        """A server's scans, newest first."""
        query = ServerScan.find(ServerScan.server_id == self._server_oid(server_id))
        if directory is not None:
            query = query.find(ServerScan.directory == directory)
        return await query.sort(("scanned_at", SortDirection.DESCENDING)).to_list()

    async def scan_latest(self, server_id: str, directory: str) -> Optional[ServerScan]:
        scans = await ServerScan.find(ServerScan.server_id == self._server_oid(server_id),
                                      ServerScan.directory == directory) \
            .sort(("scanned_at", SortDirection.DESCENDING)).limit(1).to_list()
        return scans[0] if scans else None

    async def scan_latest_identifying(self, project_id: str, directory: str) -> list[tuple[PydanticObjectId, datetime, Optional[str]]]:
        """The servers whose latest scan of `directory` identified `project_id`: (server id, scanned
        at, version id). Two queries for all servers: which scan is each one's latest (ids and dates
        only), then those scans that identified the project, with only its file."""
        collection = ServerScan.get_pymongo_collection()
        latest = [row["scan"] async for row in await collection.aggregate([
            {"$match": {"directory": directory}},
            {"$project": {"server_id": 1, "scanned_at": 1}},
            {"$sort": {"scanned_at": -1}},
            {"$group": {"_id": "$server_id", "scan": {"$first": "$_id"}}},
        ])]
        if not latest:
            return []
        found = collection.find(
            {"_id": {"$in": latest},
             "files": {"$elemMatch": {"project_id": project_id, "status": "identified"}}},
            {"server_id": 1, "scanned_at": 1, "files.$": 1})
        return [(doc["server_id"], doc["scanned_at"], doc["files"][0].get("version_id")) async for doc in found]

    async def scan_set_drift(self, server_id: str, drift: Optional[str]) -> None:
        """Record on the latest scan how it compared with the server's build."""
        latest = await self.scan_find(server_id)
        if latest:
            await ServerScan.get_pymongo_collection().update_one({"_id": latest[0].id}, {"$set": {"drift": drift}})

    async def scan_previous_drift(self, server_id: str) -> Optional[str]:
        """The comparison recorded on the scan before the latest one."""
        scans = await self.scan_find(server_id)
        return scans[1].drift if len(scans) > 1 else None

    @staticmethod
    def _server_oid(server_id: str) -> PydanticObjectId:
        if not PydanticObjectId.is_valid(server_id):
            raise HTTP_404_NOT_FOUND(f"Server '{server_id}' not found")
        return PydanticObjectId(server_id)
