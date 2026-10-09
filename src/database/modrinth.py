import asyncio
from datetime import datetime, timezone
from beanie import PydanticObjectId, SortDirection
from beanie.operators import In
from beanie.odm.queries.find import FindOne
from beanie import Document
from beanie.odm.documents import FindMany
from typing import Any, Iterable, Optional
from src.model.modrinth import (
    ModrinthProject,
    ModrinthVersion,
    ModrinthVersionList,
    ModrinthUnknown,
    ModrinthSearch,
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

    async def project_find_by_ref(self, ref: str) -> Optional[ModrinthProject]:
        """The stored project with this Modrinth project id or, failing that, this slug; None if the
        catalog has neither. The id goes first: a slug may look like another project's id."""
        found = await ModrinthProject.find_one(ModrinthProject.project_id == ref)
        return found if found is not None else await ModrinthProject.find_one(ModrinthProject.slug == ref)

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
        now = datetime.now(timezone.utc)
        for project in projects:
            project.fetched_at = now
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

    async def version_find_by_sha512(self, hashes: list[str]) -> list[ModrinthVersion]:
        """The stored versions with a file of one of these sha512 (by the files.hashes.sha512 index)."""
        if not hashes:
            return []
        return await ModrinthVersion.find(In("files.hashes.sha512", hashes)).to_list()

    async def version_find_for_game_version(self, project_ids: list[str], game_version: str) -> list[ModrinthVersion]:
        """The stored versions of these projects that list this Minecraft version, any loader or channel."""
        if not project_ids:
            return []
        return await ModrinthVersion.find(In(ModrinthVersion.project_id, project_ids),
                                          ModrinthVersion.game_versions == game_version).to_list()

    async def version_lists_find(self, project_ids: list[str], game_version: str) -> list[ModrinthVersionList]:
        """The version lists of these projects for this Minecraft version, any loaders."""
        if not project_ids:
            return []
        return await ModrinthVersionList.find(In(ModrinthVersionList.project_id, project_ids),
                                              ModrinthVersionList.game_version == game_version).to_list()

    async def version_list_save(self, record: ModrinthVersionList) -> None:
        """Store a version list, replacing the one with its project and target."""
        await ModrinthVersionList.get_pymongo_collection().replace_one(
            {"project_id": record.project_id, "game_version": record.game_version, "loaders": record.loaders},
            record.model_dump(exclude={"id", "revision_id"}), upsert=True)

    async def version_find_listed(self, project_id: str, version_ids: list[str]) -> list[ModrinthVersion]:
        """The stored versions of a project among these ids."""
        if not version_ids:
            return []
        return await ModrinthVersion.find(ModrinthVersion.project_id == project_id,
                                          In(ModrinthVersion.version_id, version_ids)).to_list()

    async def search_get(self, key: str, since: datetime) -> Optional[list[dict[str, Any]]]:
        """The hits stored for this search at or after `since`; None if there are none."""
        found = await ModrinthSearch.find_one(ModrinthSearch.key == key, ModrinthSearch.at >= since)
        return found.hits if found is not None else None

    async def search_save(self, key: str, hits: list[dict[str, Any]], when: datetime) -> None:
        await ModrinthSearch.get_pymongo_collection().update_one(
            {"key": key}, {"$set": {"hits": hits, "at": when}}, upsert=True)

    async def unknown_find(self, project_ids: list[str], since: datetime) -> set[str]:
        """The ids among these Modrinth did not know at or after `since`."""
        if not project_ids:
            return set()
        found = await ModrinthUnknown.find(In(ModrinthUnknown.project_id, project_ids), ModrinthUnknown.checked_at >= since).to_list()
        return {u.project_id for u in found}

    async def unknown_add(self, project_ids: list[str], when: datetime) -> None:
        """Remember that Modrinth did not know these ids at `when`."""
        collection = ModrinthUnknown.get_pymongo_collection()
        for project_id in project_ids:
            await collection.update_one({"project_id": project_id}, {"$set": {"checked_at": when}}, upsert=True)

    async def version_find_by_project(self, project_id: str, limit: int) -> list[ModrinthVersion]:
        """The stored versions of a project, newest first, at most `limit` (by the project_id index)."""
        return await ModrinthVersion.find(ModrinthVersion.project_id == project_id) \
            .sort(("date_published", SortDirection.DESCENDING)).limit(limit).to_list()

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