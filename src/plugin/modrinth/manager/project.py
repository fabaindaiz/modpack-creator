from src.model.modrinth.project import ModrinthProject
from src.plugin.modrinth.client.api import ModrinthAPI, ModrinthCDN
from src.plugin.modrinth.database.project import ModrinthProjectDatabase
from src.library.dependency.core import Dependent, dependent
from src.library.utils.singleton import singleton

@singleton
@dependent()
class ModrinthProjectManager(Dependent):
    def __init__(self):
        self.modrinth_api: ModrinthAPI = ModrinthAPI()
        self.modrinth_project_db: ModrinthProjectDatabase = ModrinthProjectDatabase()
        
    async def project_add_one(self, slug: str) -> ModrinthProject:
        """
        Add a project to the database.
        
        :param project: The project data to add.
        """
        project_data = await self.modrinth_api.project_get_one(slug)
        with self.modrinth_project_db as project_db:
            project = await project_db.project_create(
                id=project_data['id'],
                data=project_data,
                overwrite=True
            )
        return project