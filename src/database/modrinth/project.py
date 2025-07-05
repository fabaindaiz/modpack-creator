from src.model.modrinth.project import ModrinthProject
from src.library.tinydb import TinyDBDatabase, where
from src.library.utils import getenv

MODRINTH_PROJECT_FILE = getenv("MODRINTH_PROJECT_FILE", "modrinth/project.json")

class ModrinthProjectDatabase(TinyDBDatabase[ModrinthProject]):
    def __init__(self):
        super().__init__(model_cls=ModrinthProject, file=MODRINTH_PROJECT_FILE)

    def __enter__(self) -> 'ModrinthProjectDatabase':
        super().__enter__()
        return self
    
    def __exit__(self, exc_type, exc_value, traceback) -> None:
        super().__exit__(exc_type, exc_value, traceback)
    
    async def project_exists(self, id: str) -> bool:
        """
        Check if a project with the given slug exists in the database.
        """
        return self.exists(where('id') == id)
    
    async def project_create(self, id: str, data: dict, overwrite: bool = False) -> ModrinthProject:
        """
        Create a new project in the database.
        """
        if overwrite:
            self.delete(where('id') == id)
        if await self.project_exists(id):
            raise ValueError(f"Project with ID '{id}' already exists.")
        data.update({"id": id})
        return self.insert_one(data)

    async def project_get_id(self, id: str) -> ModrinthProject:
        """
        Get a project by its ID.
        """
        if not await self.project_exists(id):
            raise ValueError(f"Project with ID '{id}' does not exist.")
        return self.get_one(where('id') == id)

    async def project_update(self, id: str, data: dict) -> ModrinthProject:
        """
        Update an existing project in the database.
        """
        if not await self.project_exists(id):
            raise ValueError(f"Project with ID '{id}' does not exist.")
        return self.update_one(data, where('id') == id)
    
    async def project_delete(self, id: str) -> int:
        return self.delete(where('id') == id)