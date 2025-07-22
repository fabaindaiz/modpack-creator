from beanie import PydanticObjectId
from beanie.operators import In
from beanie.odm.documents import FindMany
from typing import Iterable, Optional
from src.model.modrinth import (
    ModrinthProject,
    ModrinthVersion,
)
from src.model.utils import MinecraftLoaders
from src.exceptions import *

class ModrinthDatabase():
    def project_find(self,
            project_ids: Optional[list[str]] = None,
            game_version: Optional[str] = None,
            loader: Optional[MinecraftLoaders] = None
            ) -> FindMany[ModrinthProject]:
        """
        Get all modrinth projects.
        
        :return: An iterable of ModrinthProject objects.
        """
        query = ModrinthProject.find()
        if project_ids:
            query = query.find(In(ModrinthProject.id, (PydanticObjectId(id) for id in project_ids)))
        if game_version:
            query = query.find(In(ModrinthProject.game_versions, [game_version]))
        if loader:
            query = query.find(In(ModrinthProject.loaders, [loader]))
        return query

    async def project_get(self, project_id: str) -> ModrinthProject:
        """
        Get a single project by its ID.
        
        :param id: The ID of the project to retrieve.
        :return: The ModrinthProject object.
        """
        document = await ModrinthProject.get(project_id)
        if not document:
            raise HTTP_404_NOT_FOUND(f"Project with ID '{project_id}' not found.")
        return document

    async def project_insert(self, project: ModrinthProject) -> ModrinthProject:
        """
        Add a single project to the database.
        """
        return await ModrinthProject.create(project)

    async def project_insert_many(self, projects: Iterable[ModrinthProject]) -> Iterable[ModrinthProject]:
        """
        Add multiple projects to the database.
        
        :param projects: The list of ModrinthProject objects to add.
        """
        result = await ModrinthProject.insert_many(projects)
        return await self.project_find(project_ids=result.inserted_ids).to_list()
    
    async def project_save(self, project: ModrinthProject, project_id: Optional[PydanticObjectId] = None) -> ModrinthProject:
        """
        Add a project to the database.
        
        :param project: The ModrinthProject object to add.
        :return: The created ModrinthProject object.
        """
        if project_id:
            project.id = project_id
        await project.save()
        return project

    def version_find(self,
            version_ids: Optional[list[str]] = None,
            ) -> FindMany[ModrinthVersion]:
        """
        Get all modrinth versions.
        
        :return: An iterable of ModrinthVersion objects.
        """
        query = ModrinthVersion.find()
        if version_ids:
            query = query.find(In(ModrinthVersion.id, (PydanticObjectId(id) for id in version_ids)))
        return query
    
    async def version_get(self, version_id: str) -> ModrinthVersion:
        """
        Get a single version by its ID.
        
        :param id: The ID of the version to retrieve.
        :return: The ModrinthVersion object.
        """
        document = await ModrinthVersion.get(version_id)
        if not document:
            raise HTTP_404_NOT_FOUND(f"Version with ID '{version_id}' not found.")
        return document
    
    async def version_insert(self, version: ModrinthVersion) -> ModrinthVersion:
        """
        Add a single version to the database.
        
        :param version: The ModrinthVersion object to add.
        :return: The created ModrinthVersion object.
        """
        return await ModrinthVersion.create(version)
    
    async def version_insert_many(self, versions: Iterable[ModrinthVersion]) -> Iterable[ModrinthVersion]:
        """
        Add multiple versions to the database.
        
        :param versions: The list of ModrinthVersion objects to add.
        """
        result = await ModrinthVersion.insert_many(versions)
        return await self.version_find().to_list()

    async def version_save(self, version: ModrinthVersion, version_id: Optional[PydanticObjectId] = None) -> ModrinthVersion:
        """
        Add a version to the database.
        
        :param version: The ModrinthVersion object to add.
        :return: The created ModrinthVersion object.
        """
        if version_id:
            version.id = PydanticObjectId(version_id)
        await version.save()
        return version