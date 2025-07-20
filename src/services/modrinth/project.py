from src.model.modrinth.project import ModrinthProject
from src.library.api.client.modrinth import ModrinthAPI

class ModrinthProjectService():
    def __init__(self,
            modrinthAPI: ModrinthAPI,
            ) -> None:
        self.modrinthAPI = modrinthAPI
    
    async def project_get_one(self, slug: str) -> ModrinthProject:
        """
        Get a project by its slug.
        
        :param slug: The slug of the project to retrieve.
        :return: The ModrinthProject object.
        """
        project_data = await self.modrinthAPI.project_get_one(slug)
        return ModrinthProject(**project_data)
        
    async def project_add_one(self, slug: str) -> ModrinthProject:
        """
        Add a project to the database.
        
        :param project: The project data to add.
        """
        project_data = await self.modrinthAPI.project_get_one(slug)
        project = ModrinthProject(**project_data)
        return await ModrinthProject.insert(project)