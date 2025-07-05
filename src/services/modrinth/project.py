from src.database.modrinth.project import ModrinthProjectDatabase
from src.model.modrinth.project import ModrinthProject
from src.library.api.client.modrinth import ModrinthAPI

class ModrinthProjectService():
    def __init__(self,
            modrinthAPI: ModrinthAPI,
            modrinthProjectDB: ModrinthProjectDatabase
            ) -> None:
        self.modrinthAPI = modrinthAPI
        self.modrinthProjectDB = modrinthProjectDB
        
    async def project_add_one(self, slug: str) -> ModrinthProject:
        """
        Add a project to the database.
        
        :param project: The project data to add.
        """
        project_data = await self.modrinthAPI.project_get_one(slug)
        with self.modrinthProjectDB as project_db:
            project = await project_db.project_create(
                id=project_data['id'],
                data=project_data,
                overwrite=True)
        return project