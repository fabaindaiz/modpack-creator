import asyncio
from typing import Iterable, Optional, Sequence
from src.library.api.client.modrinth import ModrinthAPI
from src.database.modrinth import ModrinthDatabase
from src.model.modrinth import ModrinthProject, ModrinthVersion
from src.exceptions import *

class ModrinthService():
    def __init__(self,
            modrinthAPI: ModrinthAPI,
            modrinthDB: ModrinthDatabase,
            ) -> None:
        self.modrinthAPI = modrinthAPI
        self.modrinthDB = modrinthDB
    
    async def project_create(self, slug: str) -> ModrinthProject:
        """
        Add a project to the database.
        
        :param slug: The slug of the project to add.
        :return: The created ModrinthProject object.
        """
        project = await self.modrinthAPI.project_get(slug)
        return await self.modrinthDB.project_insert(project)
    
    async def project_create_many(self, slugs: list[str]) -> Iterable[ModrinthProject]:
        """
        Add multiple projects to the database.
        
        :param slugs: The list of project slugs to add.
        """
        projects = await self.modrinthAPI.project_get_many(slugs)
        return await self.modrinthDB.project_insert_many(projects)
    
    async def project_update(self, project_id: str) -> ModrinthProject:
        """
        Update a project in the database.
        
        :param slug: The slug of the project to update.
        :return: The updated ModrinthProject object.
        """
        document = await self.modrinthDB.project_get(project_id)
        project = await self.modrinthAPI.project_get(document.slug)
        return await self.modrinthDB.project_save(document, project.id)

    async def project_update_many(self, project_ids: list[str]) -> Iterable[ModrinthProject]:
        """
        Update multiple projects in the database.
        
        :param slugs: The list of project slugs to update.
        """
        documents = await self.modrinthDB.project_find(project_ids=project_ids).to_list()
        projects = await self.modrinthAPI.project_get_many(slugs=[doc.slug for doc in documents])
        return await asyncio.gather(*[
            self.modrinthDB.project_save(document, project.id)
            for document, project in zip(documents, projects)
            if document.slug == project.slug
        ])

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
    
    async def project_version_update_filter(self, slug: str, loaders: Sequence[str] = (), game_versions: Sequence[str] = (), featured: bool = True) -> Iterable[ModrinthVersion]:
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