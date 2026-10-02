import asyncio
from typing import Any, Iterable, Optional, Sequence
from src.library.resolution import LOADER_ACCEPTS, TYPE_LOADERS
from src.library.api.client.modrinth import ModrinthAPI
from src.database.modrinth import ModrinthDatabase
from src.model.modrinth import ModrinthProject, ModrinthVersion
from src.model.modrinth.version import ModrinthVersionDependency
from src.exceptions import *

# The project types a pack takes (resolution's SUPPORTED_TYPES), as Modrinth's search names them.
SEARCH_TYPES = ("mod", "shader", "resourcepack", "datapack")

class ModrinthService():
    def __init__(self,
            modrinthAPI: ModrinthAPI,
            modrinthDB: ModrinthDatabase,
            ) -> None:
        self.modrinthAPI = modrinthAPI
        self.modrinthDB = modrinthDB

    async def project_find(self, game_version: Optional[str] = None, loader: Optional[str] = None) -> list[ModrinthProject]:
        return await self.modrinthDB.project_find(game_version=game_version, loader=loader).to_list()

    async def search(self, query: str, game_version: Optional[str], loader: Optional[str], limit: int = 10) -> list[dict[str, Any]]:
        """
        Search Modrinth for projects to add to a pack, as Modrinth returns the hits.

        Facets are ANDed groups of ORed clauses: any type a pack takes, the game version, and the
        loaders that run in the pack. Shaders, resource packs and datapacks have loaders of their
        own, so those are in the loader group: a Fabric filter alone would hide them all.
        """
        facets = [[f"project_type:{t}" for t in SEARCH_TYPES]]
        if game_version:
            facets.append([f"versions:{game_version}"])
        if loader:
            loaders = set(LOADER_ACCEPTS.get(loader, {loader})).union(*TYPE_LOADERS.values())
            facets.append([f"categories:{name}" for name in sorted(loaders)])
        return await self.modrinthAPI.search(query, facets, limit)

    async def projects_by_id(self, project_ids: list[str]) -> dict[str, ModrinthProject]:
        """The catalog's projects by their Modrinth project id; what is not stored is absent."""
        found = await self.modrinthDB.project_find_by_modrinth_id(project_ids)
        return {project.project_id: project for project in found}

    async def project_get(self, project_id: str) -> ModrinthProject:
        return await self.modrinthDB.project_get(project_id)
    
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
        Add multiple projects to the database, refreshing those already in it.
        
        :param slugs: The list of project slugs to add.
        """
        projects = await self.modrinthAPI.project_get_many(slugs)
        return await self.modrinthDB.project_upsert_many(projects)
    
    async def project_update(self, project_id: str) -> ModrinthProject:
        """
        Update a project in the database with what Modrinth returns now.
        
        :param project_id: The database ID of the project to update.
        :return: The updated ModrinthProject object.
        """
        document = await self.modrinthDB.project_get(project_id)
        project = await self.modrinthAPI.project_get(document.slug)
        return await self.modrinthDB.project_save(project, document.id)

    async def project_update_many(self, project_ids: list[str]) -> Iterable[ModrinthProject]:
        """
        Update multiple projects in the database with what Modrinth returns now.
        
        Modrinth does not promise any order, so projects are matched by their Modrinth project_id,
        and one it returns that was not asked for is ignored.

        :param project_ids: The database IDs of the projects to update.
        """
        documents = await self.modrinthDB.project_find(project_ids=project_ids).to_list()
        asked = {doc.project_id for doc in documents}
        projects = await self.modrinthAPI.project_get_many(slugs=[doc.slug for doc in documents])
        return await self.modrinthDB.project_upsert_many(p for p in projects if p.project_id in asked)

    async def version_dependencies(self, version_id: str) -> list[ModrinthVersionDependency]:
        """
        The dependencies of one version, with the version, the versions it pins and every project
        it names stored in the catalog.

        Per version, never per project: /project/{id}/dependencies mixes the dependencies of every
        version of a project, for every game version and loader, incompatible ones included.

        :param version_id: The Modrinth ID of the version.
        :return: Its dependencies, as Modrinth declares them.
        """
        [version] = await self.modrinthAPI.version_get_many([version_id])
        pinned = [d.version_id for d in version.dependencies if d.version_id]
        versions = [version, *(await self.modrinthAPI.version_get_many(pinned) if pinned else [])]
        named = {d.project_id for d in version.dependencies if d.project_id}
        project_ids = sorted({v.project_id for v in versions} | named)
        await self.modrinthDB.project_upsert_many(await self.modrinthAPI.project_get_many(project_ids))
        await self.modrinthDB.version_upsert_many(versions)
        return version.dependencies
    
    async def project_version_update_filter(self, slug: str, loaders: Sequence[str] = (), game_versions: Sequence[str] = (), featured: Optional[bool] = None) -> Iterable[ModrinthVersion]:
        """
        Get all versions for a project.
        """
        project = await ModrinthProject.find_one({"slug": slug})
        if not project:
            raise ValueError(f"Project with slug '{slug}' not found.")
        versions = await self.modrinthAPI.project_versions(slug, loaders, game_versions, featured)
        return await self.modrinthDB.version_upsert_many(versions)

    async def project_version_update_all(self, slug: str) -> Iterable[ModrinthVersion]:
        """
        Get all versions for a project.
        """
        project = await ModrinthProject.find_one({"slug": slug})
        if not project:
            raise ValueError(f"Project with slug '{slug}' not found.")
        versions = await self.modrinthAPI.version_get_many(project.versions)
        return await self.modrinthDB.version_upsert_many(versions)