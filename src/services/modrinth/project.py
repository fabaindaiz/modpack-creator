from beanie.operators import In
from beanie.odm.documents import FindMany
from typing import AsyncGenerator, Iterable, Optional
from src.model.modrinth import ModrinthProject, ModrinthVersion
from src.model.utils import MinecraftLoaders
from src.library.api.client.modrinth import ModrinthAPI
from src.exceptions import *

class ModrinthProjectService():
    def __init__(self,
            modrinthAPI: ModrinthAPI,
            ) -> None:
        self.modrinthAPI = modrinthAPI
    
    async def project_get_one(self, id: str) -> ModrinthProject:
        """
        Get a single project by its ID.
        
        :param id: The ID of the project to retrieve.
        :return: The ModrinthProject object.
        """
        document = await ModrinthProject.get(id)
        if not document:
            raise HTTP_404_NOT_FOUND(f"Project with ID '{id}' not found.")
        return document

    def project_get_many(self,
            game_version: Optional[str] = None,
            loader: Optional[MinecraftLoaders] = None
            ) -> FindMany[ModrinthProject]:
        """
        Get all modrinth projects.
        
        :return: An iterable of ModrinthProject objects.
        """
        query = ModrinthProject.find()
        if game_version:
            query = query.find(In(ModrinthProject.game_versions, [game_version]))
        if loader:
            query = query.find(In(ModrinthProject.loaders, [loader]))
        return query
    
    async def project_create_one(self, slug: str) -> ModrinthProject:
        """
        Add a project to the database.
        
        :param slug: The slug of the project to add.
        :return: The created ModrinthProject object.
        """
        project = await self.modrinthAPI.project_get_one(slug)
        return await ModrinthProject.create(project)
    
    async def project_create_many(self, slugs: list[str]) -> Iterable[ModrinthProject]:
        """
        Add multiple projects to the database.
        
        :param slugs: The list of project slugs to add.
        """
        projects = await self.modrinthAPI.project_get_many(slugs)
        await ModrinthProject.insert_many(projects)
        return projects
    
    async def project_update_one(self, id: str) -> ModrinthProject:
        """
        Update a project in the database.
        
        :param slug: The slug of the project to update.
        :return: The updated ModrinthProject object.
        """
        document = await self.project_get_one(id)
        project = await self.modrinthAPI.project_get_one(document.slug)
        project.id = document.id
        await project.save()
        return project

    async def project_update_many(self, ids: list[str]) -> Iterable[ModrinthProject]:
        """
        Update multiple projects in the database.
        
        :param slugs: The list of project slugs to update.
        """
        documents = await ModrinthProject.find_many(In(ModrinthProject.id, ids)).to_list()
        projects = await self.modrinthAPI.project_get_many([doc.slug for doc in documents])
        for document, project in zip(documents, projects):
            project.id = document.id
            await project.save()
        return projects
    
    async def project_dependencies(self, slug: str) -> Iterable[ModrinthProject]:
        """
        Get all dependencies for a project.
        
        :param slug: The slug of the project to get dependencies for.
        """
        projects, versions = await self.modrinthAPI.project_dependencies(slug)
        for project in projects:
            await ModrinthProject.find_one({"slug": project.slug}).upsert(on_insert=project)
        for version in versions:
            await ModrinthVersion.find_one({"id": version.id}).upsert(on_insert=version)
        return projects
    
    async def project_version_update_filter(self, slug: str, loaders: list[str] = [], game_versions: list[str] = [], featured: bool = True) -> Iterable[ModrinthVersion]:
        """
        Get all versions for a project.
        """
        project = await ModrinthProject.find_one({"slug": slug})
        if not project:
            raise ValueError(f"Project with slug '{slug}' not found.")
        versions = await self.modrinthAPI.project_versions(slug, loaders, game_versions, featured)
        for version in versions:
            await ModrinthVersion.find_one({"id": version.id}).upsert(on_insert=version)
        return versions

    async def project_version_update_all(self, slug: str) -> Iterable[ModrinthVersion]:
        """
        Get all versions for a project.
        """
        project = await ModrinthProject.find_one({"slug": slug})
        if not project:
            raise ValueError(f"Project with slug '{slug}' not found.")
        versions = await self.modrinthAPI.version_get_many(project.versions)
        for version in versions:
            await ModrinthVersion.find_one({"id": version.id}).upsert(on_insert=version)
        return versions