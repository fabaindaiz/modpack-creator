from src.model.modrinth.project import ModrinthProject
from src.library.api.client.modrinth import ModrinthAPI

class ModrinthProjectService():
    def __init__(self,
            modrinthAPI: ModrinthAPI,
            ) -> None:
        self.modrinthAPI = modrinthAPI
        
    async def project_add_one(self, slug: str) -> ModrinthProject:
        """
        Add a project to the database.
        
        :param project: The project data to add.
        """
        project = await self.modrinthAPI.project_get_one(slug)
        return await ModrinthProject.create(project)