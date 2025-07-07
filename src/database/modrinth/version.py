from src.model.modrinth.version import ModrinthVersion
from src.library.tinydb import TinyDBDatabase, where
from src.library.utils import getenv

MODRINTH_VERSION_FILE = getenv("MODRINTH_VERSION_FILE", "data/modrinth/version.json")

class ModrinthVersionDatabase(TinyDBDatabase[ModrinthVersion]):
    def __init__(self):
        super().__init__(model_cls=ModrinthVersion, file=MODRINTH_VERSION_FILE)

    def __enter__(self) -> 'ModrinthVersionDatabase':
        super().__enter__()
        return self
    
    def __exit__(self, exc_type, exc_value, traceback) -> None:
        super().__exit__(exc_type, exc_value, traceback)
    
    async def version_exists(self, id: str) -> bool:
        """
        Check if a version with the given ID exists in the database.
        """
        return self.exists(where('id') == id)

    async def version_create(self, id: str,data: dict, overwrite: bool = False) -> ModrinthVersion:
        """
        Create a new version in the database.
        """
        if overwrite:
            self.delete(where('id') == id)
        if await self.version_exists(id):
            raise ValueError(f"Version with ID '{id}' already exists.")
        data.update({"id": id})
        return self.insert_one(data)

    async def version_get_id(self, id: str) -> ModrinthVersion:
        """
        Get a version by its ID.
        """
        if not await self.version_exists(id):
            raise ValueError(f"Version with ID '{id}' does not exist.")
        return self.get_one(where('id') == id)

    async def version_update(self, id: str, data: dict) -> ModrinthVersion:
        """
        Update an existing version in the database.
        """
        if not await self.version_exists(id):
            raise ValueError(f"Version with ID '{id}' does not exist.")
        return self.update_one(data, where('id') == id)

    async def version_delete(self, id: str) -> int:
        return self.delete(where('id') == id)