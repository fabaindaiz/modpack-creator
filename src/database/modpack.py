from beanie import PydanticObjectId, SortDirection
from beanie.operators import In
from beanie.odm.documents import FindMany, DeleteResult
from typing import Iterable, Optional
from pymongo.errors import DuplicateKeyError
from src.model.modpack import (
    ModpackBuild,
    ModpackBundle,
    ModpackBundlePatch,
    ModpackReplacement,
    PackProject,
    ProjectOrigin,
)
from src.exceptions import *

class ModpackDatabase():
    def bundle_find(self,
            bundle_ids: Optional[list[str]] = None,
            ) -> FindMany[ModpackBundle]:
        """
        Get all modpack bundles.
        
        :return: An iterable of ModpackBundle objects.
        """
        query = ModpackBundle.find()
        if bundle_ids:
            query = query.find(In(ModpackBundle.id, (PydanticObjectId(id) for id in bundle_ids)))
        return query

    async def bundle_get(self, bundle_id: str) -> ModpackBundle:
        """
        Get a single modpack bundle by its ID.
        
        :param id: The ID of the bundle to retrieve.
        :return: The ModpackBundle object.
        """
        document = await ModpackBundle.get(self._bundle_oid(bundle_id))
        if not document:
            raise HTTP_404_NOT_FOUND(f"Bundle '{bundle_id}' not found")
        return document
    
    async def bundle_update(self, bundle_id: str, patch: ModpackBundlePatch) -> ModpackBundle:
        """
        Update an existing modpack bundle.
        
        :param bundle_id: The ID of the bundle to update.
        :param patch: The patch containing the updated fields.
        :return: The updated ModpackBundle object.
        """
        document = await self.bundle_get(bundle_id)
        return await document.set(patch.model_dump(exclude_unset=True))

    async def bundle_insert(self, bundle: ModpackBundle) -> ModpackBundle:
        """
        Create a new modpack bundle.
        
        :param bundle: The ModpackBundle object to create.
        :return: The created ModpackBundle object.
        """
        return await ModpackBundle.create(bundle)
    
    async def bundle_projects_add(self, bundle_id: str, projects: list[PackProject]) -> list[PackProject]:
        """
        Add projects to a bundle, each only if the bundle does not have it yet.

        One atomic update per project: two concurrent additions both stay (a read-modify-write
        of the list would keep only one).

        :return: The bundle's projects afterwards.
        """
        oid = self._bundle_oid(bundle_id)
        collection = ModpackBundle.get_pymongo_collection()
        for project in projects:
            await collection.update_one(
                {"_id": oid, "projects.project_id": {"$ne": project.project_id}},
                {"$push": {"projects": project.model_dump(mode="json")}})
        return (await self.bundle_get(bundle_id)).projects

    async def bundle_project_remove(self, bundle_id: str, project_id: str) -> list[PackProject]:
        """Remove a project from a bundle; 404 if the bundle does not have it."""
        oid = self._bundle_oid(bundle_id)
        result = await ModpackBundle.get_pymongo_collection().update_one(
            {"_id": oid, "projects.project_id": project_id},
            {"$pull": {"projects": {"project_id": project_id}}})
        if not result.modified_count:
            raise HTTP_404_NOT_FOUND(f"Bundle '{bundle_id}' has no project '{project_id}'")
        return (await self.bundle_get(bundle_id)).projects

    async def bundle_pack_replace(self, bundle_id: str, projects: list[PackProject]) -> list[PackProject]:
        """
        Replace what the pack brings, keeping what the admin added (d-7985ee-b15dd4).

        A pipeline update, computed by MongoDB on the stored list in one step: the admin's
        projects stay, and a new pack project the admin already has stays the admin's.
        """
        oid = self._bundle_oid(bundle_id)
        admin = {"$filter": {"input": {"$ifNull": ["$projects", []]},
                             "cond": {"$eq": ["$$this.origin", ProjectOrigin.admin.value]}}}
        new = [p.model_dump(mode="json") for p in projects]
        result = await ModpackBundle.get_pymongo_collection().update_one({"_id": oid}, [
            {"$set": {"projects": {"$concatArrays": [admin, {"$filter": {
                "input": {"$literal": new},
                "cond": {"$not": [{"$in": ["$$this.project_id", {"$map": {"input": admin, "in": "$$this.project_id"}}]}]},
            }}]}}}])
        if not result.matched_count:
            raise HTTP_404_NOT_FOUND(f"Bundle '{bundle_id}' not found")
        return (await self.bundle_get(bundle_id)).projects

    async def bundle_latest_build(self, bundle_id: str, number: int) -> None:
        """Move the latest pointer forward to `number`; never back ($max)."""
        await ModpackBundle.get_pymongo_collection().update_one(
            {"_id": self._bundle_oid(bundle_id)}, {"$max": {"latest_build": number}})

    async def bundle_recommend_build(self, bundle_id: str, number: int) -> ModpackBundle:
        """Point recommended at build `number`, older or newer."""
        await ModpackBundle.get_pymongo_collection().update_one(
            {"_id": self._bundle_oid(bundle_id)}, {"$set": {"recommended_build": number}})
        return await self.bundle_get(bundle_id)

    async def build_find(self, bundle_id: str) -> list[ModpackBuild]:
        """A bundle's builds, newest first."""
        return await ModpackBuild.find(ModpackBuild.bundle_id == self._bundle_oid(bundle_id)) \
            .sort(("number", SortDirection.DESCENDING)).to_list()

    async def build_get(self, bundle_id: str, number: int) -> ModpackBuild:
        build = await ModpackBuild.find_one(ModpackBuild.bundle_id == self._bundle_oid(bundle_id),
                                            ModpackBuild.number == number)
        if not build:
            raise HTTP_404_NOT_FOUND(f"Bundle '{bundle_id}' has no build {number}")
        return build

    async def build_insert(self, build: ModpackBuild) -> ModpackBuild:
        """Store a build. Its number is unique per bundle: a build created at the same time with the
        same number is refused with 409, never stored twice."""
        try:
            return await ModpackBuild.insert(build)
        except DuplicateKeyError as error:
            raise HTTP_409_CONFLICT(f"Build {build.number} was just created by another request; try again") from error

    @staticmethod
    def _bundle_oid(bundle_id: str) -> PydanticObjectId:
        if not PydanticObjectId.is_valid(bundle_id):
            raise HTTP_404_NOT_FOUND(f"Bundle '{bundle_id}' not found")
        return PydanticObjectId(bundle_id)

    async def replacement_find(self) -> list[ModpackReplacement]:
        """The global replacement rules."""
        return await ModpackReplacement.find().to_list()

    async def replacement_set(self, rule: ModpackReplacement) -> ModpackReplacement:
        """Create the global rule for its project, or replace the one it has."""
        await ModpackReplacement.find_one(ModpackReplacement.project_id == rule.project_id).upsert(
            {"$set": {"replacement": rule.replacement}}, on_insert=rule)
        stored = await ModpackReplacement.find_one(ModpackReplacement.project_id == rule.project_id)
        assert stored is not None
        return stored

    async def replacement_delete(self, project_id: str) -> bool:
        """Delete the global rule for a project; whether there was one."""
        result = await ModpackReplacement.find_one(ModpackReplacement.project_id == project_id).delete()
        return bool(result and result.deleted_count)
