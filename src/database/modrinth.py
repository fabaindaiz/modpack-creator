import asyncio
from beanie import PydanticObjectId
from beanie.operators import In
from beanie.odm.queries.find import FindOne
from beanie import Document
from beanie.odm.documents import FindMany
from typing import Iterable, Optional
from src.model.modrinth import (
    ModrinthProject,
    ModrinthVersion,
)
from src.exceptions import *

class ModrinthDatabase():
    @staticmethod
    async def _upsert[D: Document](query: FindOne[D], document: D, keep: frozenset[str] = frozenset()) -> None:
        """Insert the document, or set every field of the one the query finds to its values.

        A field in `keep` that is None here is left as stored: what a fuller read saved is not
        erased by a refresh that never carried it."""
        values = document.model_dump(exclude={"id", "revision_id"})
        for name in keep:
            if values.get(name) is None:
                values.pop(name, None)
        await query.upsert({"$set": values}, on_insert=document)

    def project_find(self,
            project_ids: Optional[list[str]] = None,
            game_version: Optional[str] = None,
            loader: Optional[str] = None
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

    async def project_find_by_modrinth_id(self, project_ids: list[str]) -> list[ModrinthProject]:
        """The stored projects with these Modrinth project ids; the ones not stored are absent."""
        if not project_ids:
            return []
        return await ModrinthProject.find(In(ModrinthProject.project_id, project_ids)).to_list()

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

    async def project_upsert_many(self, projects: Iterable[ModrinthProject]) -> list[ModrinthProject]:
        """
        Store each project as Modrinth returned it: insert it, or refresh the one with its project_id.

        One write per project, so a project already stored neither stops nor loses the others
        (an ordered insert_many aborts at the first duplicate key).

        :param projects: The ModrinthProject objects to store.
        :return: The stored ModrinthProject objects.
        """
        # One write per key: two concurrent upserts of one key race on the unique index.
        projects = list({p.project_id: p for p in projects}.values())
        await asyncio.gather(*[self._upsert(ModrinthProject.find_one(ModrinthProject.project_id == project.project_id), project) for project in projects])
        return await ModrinthProject.find(In(ModrinthProject.project_id, [p.project_id for p in projects])).to_list()

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
    
    async def version_find_by_version_id(self, version_id: str) -> Optional[ModrinthVersion]:
        """The stored version with this Modrinth version id, or None if the catalog does not have it."""
        return await ModrinthVersion.find_one(ModrinthVersion.version_id == version_id)

    async def version_find_by_version_ids(self, version_ids: list[str]) -> list[ModrinthVersion]:
        """The stored versions with these Modrinth version ids; the ones not stored are absent."""
        if not version_ids:
            return []
        return await ModrinthVersion.find(In(ModrinthVersion.version_id, version_ids)).to_list()

    async def version_insert(self, version: ModrinthVersion) -> ModrinthVersion:
        """
        Add a single version to the database.
        
        :param version: The ModrinthVersion object to add.
        :return: The created ModrinthVersion object.
        """
        return await ModrinthVersion.create(version)
    
    async def version_upsert_many(self, versions: Iterable[ModrinthVersion]) -> list[ModrinthVersion]:
        """
        Store each version as Modrinth returned it: insert it, or refresh the one with its version_id.

        :param versions: The ModrinthVersion objects to store.
        :return: The stored ModrinthVersion objects.
        """
        versions = list({v.version_id: v for v in versions}.values())
        await asyncio.gather(*[self._upsert(ModrinthVersion.find_one(ModrinthVersion.version_id == version.version_id), version, keep=frozenset({"changelog"})) for version in versions])
        return await ModrinthVersion.find(In(ModrinthVersion.version_id, [v.version_id for v in versions])).to_list()

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