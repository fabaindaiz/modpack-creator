from src.model.pterodactyl.server import PterodactylServer
from src.library.tinydb import TinyDBDatabase, where
from src.library.utils import getenv

PTERODACTYL_SERVER_FILE = getenv("PTERODACTYL_SERVER_FILE", "data/pterodactyl/server.json")

class PterodactylServerDatabase(TinyDBDatabase[PterodactylServer]):
    def __init__(self):
        super().__init__(model_cls=PterodactylServer, file=PTERODACTYL_SERVER_FILE)

    def __enter__(self) -> 'PterodactylServerDatabase':
        super().__enter__()
        return self
    
    def __exit__(self, exc_type, exc_value, traceback) -> None:
        super().__exit__(exc_type, exc_value, traceback)
    
    async def server_exists(self, id: str) -> bool:
        """
        Check if a server with the given ID exists in the database.
        """
        return self.exists(where('id') == id)

    async def server_create(self, id: str,data: dict, overwrite: bool = False) -> PterodactylServer:
        """
        Create a new server in the database.
        """
        if overwrite:
            self.delete(where('id') == id)
        if await self.server_exists(id):
            raise ValueError(f"Server with ID '{id}' already exists.")
        data.update({"id": id})
        return self.insert_one(data)

    async def server_get_id(self, id: str) -> PterodactylServer:
        """
        Get a server by its ID.
        """
        if not await self.server_exists(id):
            raise ValueError(f"Server with ID '{id}' does not exist.")
        return self.get_one(where('id') == id)

    async def server_update(self, id: str, data: dict) -> PterodactylServer:
        """
        Update an existing server in the database.
        """
        if not await self.server_exists(id):
            raise ValueError(f"Server with ID '{id}' does not exist.")
        return self.update_one(data, where('id') == id)

    async def server_delete(self, id: str) -> int:
        return self.delete(where('id') == id)