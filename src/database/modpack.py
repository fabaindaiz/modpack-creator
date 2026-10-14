from beanie import PydanticObjectId, SortDirection
from beanie.operators import In
from beanie.odm.documents import FindMany, DeleteResult
from datetime import datetime
from typing import Any, Iterable, Optional
from pymongo.errors import DuplicateKeyError
from src.library.scan import ClaimedFile, LocalFile
from src.model.modpack import (
    BUNDLE_KEYED_FIELDS,
    BUNDLE_NULL_REMOVES,
    LoaderVersion,
    ModpackBuild,
    ModpackBundle,
    ModpackBundlePatch,
    ModpackReplacement,
    ModpackResolution,
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

    async def bundle_find_with_project(self, project_id: str) -> list[ModpackBundle]:
        """The bundles that have this Modrinth project among their projects (any origin)."""
        return await ModpackBundle.find({"projects.project_id": project_id}).to_list()

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
        Update an existing modpack bundle in one update: the fields sent are set, and the maps
        (pins, replacements, server_decisions) only at the keys sent, so a pin sent leaves the
        others alone. A null removes a pin or a decision. Every key must be a plain Modrinth id
        (the caller checks them): it becomes a key of a dotted path.

        :param bundle_id: The ID of the bundle to update.
        :param patch: The patch containing the updated fields.
        :return: The updated ModpackBundle object.
        """
        to_set: dict[str, object] = {}
        to_unset: dict[str, str] = {}
        for name, value in patch.model_dump(exclude_unset=True, mode="json").items():
            if name not in BUNDLE_KEYED_FIELDS:
                to_set[name] = value
                continue
            for key, item in value.items():
                if item is None and name in BUNDLE_NULL_REMOVES:
                    to_unset[f"{name}.{key}"] = ""
                else:
                    to_set[f"{name}.{key}"] = item
        update = {op: fields for op, fields in (("$set", to_set), ("$unset", to_unset)) if fields}
        if update:
            result = await ModpackBundle.get_pymongo_collection().update_one(
                {"_id": self._bundle_oid(bundle_id)}, update)
            if not result.matched_count:
                raise HTTP_404_NOT_FOUND(f"Bundle '{bundle_id}' not found")
        return await self.bundle_get(bundle_id)

    async def bundle_replacement_unset(self, bundle_id: str, project_id: str) -> ModpackBundle:
        """Remove the pack's own rule for one mod, touching only that key. `project_id` must be a
        plain Modrinth id. 404 if the bundle or that rule does not exist."""
        result = await ModpackBundle.get_pymongo_collection().update_one(
            {"_id": self._bundle_oid(bundle_id), f"replacements.{project_id}": {"$exists": True}},
            {"$unset": {f"replacements.{project_id}": ""}})
        if not result.matched_count:
            await self.bundle_get(bundle_id)
            raise HTTP_404_NOT_FOUND(f"Bundle '{bundle_id}' has no rule for '{project_id}'")
        return await self.bundle_get(bundle_id)

    async def bundle_insert(self, bundle: ModpackBundle) -> ModpackBundle:
        """
        Create a new modpack bundle.
        
        :param bundle: The ModpackBundle object to create.
        :return: The created ModpackBundle object.
        """
        return await ModpackBundle.create(bundle)

    async def bundle_delete_empty(self, bundle_id: str) -> bool:
        """Delete a bundle only while it is empty (no projects, local or claimed files, nor builds),
        in one atomic delete: what undoes a pack created a moment ago (d-7985ee-b8e927). Whether
        it was deleted."""
        result = await ModpackBundle.get_pymongo_collection().delete_one({
            "_id": self._bundle_oid(bundle_id), "latest_build": None, "recommended_build": None,
            "projects": {"$size": 0}, "local_files": {"$size": 0}, "claimed_files": {"$size": 0}})
        return bool(result.deleted_count)

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

    async def bundle_local_file_add(self, bundle_id: str, local: LocalFile) -> list[LocalFile]:
        """Register a local file, unless the bundle has one with that sha512 (atomic)."""
        await ModpackBundle.get_pymongo_collection().update_one(
            {"_id": self._bundle_oid(bundle_id), "local_files.sha512": {"$ne": local.sha512}},
            {"$push": {"local_files": local.model_dump()}})
        return (await self.bundle_get(bundle_id)).local_files

    async def bundle_claim_add(self, bundle_id: str, claim: ClaimedFile) -> None:
        """Record that a jar is a project, unless the bundle already has a claim for it (atomic)."""
        await ModpackBundle.get_pymongo_collection().update_one(
            {"_id": self._bundle_oid(bundle_id), "claimed_files.sha512": {"$ne": claim.sha512}},
            {"$push": {"claimed_files": claim.model_dump()}})

    async def bundle_project_remove(self, bundle_id: str, project_id: str, clear_keys: bool = False) -> list[PackProject]:
        """Remove a project from a bundle; 404 if the bundle does not have it. With `clear_keys`, its
        pin, server decision and channel exception go in the same update (d-7985ee-881689);
        `project_id` must then be a plain Modrinth id: it becomes a key of a dotted path."""
        oid = self._bundle_oid(bundle_id)
        update: dict[str, Any] = {"$pull": {"projects": {"project_id": project_id}}}
        if clear_keys:
            update["$unset"] = {f"{field}.{project_id}": "" for field in ("pins", "server_decisions", "channel_exceptions")}
        result = await ModpackBundle.get_pymongo_collection().update_one(
            {"_id": oid, "projects.project_id": project_id}, update)
        if not result.modified_count:
            raise HTTP_404_NOT_FOUND(f"Bundle '{bundle_id}' has no project '{project_id}'")
        return (await self.bundle_get(bundle_id)).projects

    async def bundle_decision_set(self, bundle_id: str, project_id: str, server: bool) -> ModpackBundle:
        """
        Decide whether one mod goes on the server, touching only that key: two decisions made at
        the same time both stay. `project_id` must be a plain Modrinth id (the caller checks it):
        it becomes a key of a dotted path.
        """
        oid = self._bundle_oid(bundle_id)
        result = await ModpackBundle.get_pymongo_collection().update_one(
            {"_id": oid}, {"$set": {f"server_decisions.{project_id}": server}})
        if not result.matched_count:
            raise HTTP_404_NOT_FOUND(f"Bundle '{bundle_id}' not found")
        return await self.bundle_get(bundle_id)

    async def bundle_decisions_set(self, bundle_id: str, decisions: dict[str, bool]) -> ModpackBundle:
        """
        Decide several mods in one update: every `server_decisions.<id>` key is set by a single
        `update_one`, so either all land or none does, and decisions made before stay. Each id
        must be a plain Modrinth id (the caller checks them): it becomes a key of a dotted path.
        """
        oid = self._bundle_oid(bundle_id)
        result = await ModpackBundle.get_pymongo_collection().update_one(
            {"_id": oid}, {"$set": {f"server_decisions.{project_id}": server for project_id, server in decisions.items()}})
        if not result.matched_count:
            raise HTTP_404_NOT_FOUND(f"Bundle '{bundle_id}' not found")
        return await self.bundle_get(bundle_id)

    async def bundle_pin_set(self, bundle_id: str, project_id: str, version_id: str) -> ModpackBundle:
        """
        Pin one mod to a version, touching only that key: two pins made at the same time both
        stay. Both ids must be plain Modrinth ids (the caller checks them): `project_id` becomes a
        key of a dotted path.
        """
        oid = self._bundle_oid(bundle_id)
        result = await ModpackBundle.get_pymongo_collection().update_one(
            {"_id": oid}, {"$set": {f"pins.{project_id}": version_id}})
        if not result.matched_count:
            raise HTTP_404_NOT_FOUND(f"Bundle '{bundle_id}' not found")
        return await self.bundle_get(bundle_id)

    async def bundle_pin_unset(self, bundle_id: str, project_id: str) -> ModpackBundle:
        """Remove one mod's pin, touching only that key. `project_id` must be a plain Modrinth id."""
        oid = self._bundle_oid(bundle_id)
        result = await ModpackBundle.get_pymongo_collection().update_one(
            {"_id": oid}, {"$unset": {f"pins.{project_id}": ""}})
        if not result.matched_count:
            raise HTTP_404_NOT_FOUND(f"Bundle '{bundle_id}' not found")
        return await self.bundle_get(bundle_id)

    async def bundle_channel_exception_set(self, bundle_id: str, project_id: str, channel: str) -> ModpackBundle:
        """Set one project's channel exception, touching only that key. `project_id` must be a plain
        Modrinth id (the caller checks it): it becomes a key of a dotted path."""
        return await self._bundle_key(bundle_id, {"$set": {f"channel_exceptions.{project_id}": channel}})

    async def bundle_channel_exception_unset(self, bundle_id: str, project_id: str) -> ModpackBundle:
        """Remove one project's channel exception, touching only that key."""
        return await self._bundle_key(bundle_id, {"$unset": {f"channel_exceptions.{project_id}": ""}})

    async def bundle_native_only_set(self, bundle_id: str, project_id: str, native: bool) -> ModpackBundle:
        """Add a project to (or remove it from) the mods moved to their native versions, atomically
        (batch C, d-7985ee-8e5f9e)."""
        op = "$addToSet" if native else "$pull"
        return await self._bundle_key(bundle_id, {op: {"native_only": project_id}})

    async def bundle_keys_set_missing(self, bundle_id: str, field: str, values: dict[str, Any]) -> dict[str, Any]:
        """Set each key of a bundle's map (`server_decisions`, `channel_exceptions`) only where it is
        absent, key by key: a value someone set, even at the same time, is never overwritten. The
        keys must be plain Modrinth ids. Returns the keys written."""
        oid = self._bundle_oid(bundle_id)
        collection = ModpackBundle.get_pymongo_collection()
        written: dict[str, Any] = {}
        for key, value in values.items():
            result = await collection.update_one({"_id": oid, f"{field}.{key}": {"$exists": False}},
                                                 {"$set": {f"{field}.{key}": value}})
            if result.modified_count:
                written[key] = value
        return written

    async def _bundle_key(self, bundle_id: str, update: dict[str, Any]) -> ModpackBundle:
        result = await ModpackBundle.get_pymongo_collection().update_one({"_id": self._bundle_oid(bundle_id)}, update)
        if not result.matched_count:
            raise HTTP_404_NOT_FOUND(f"Bundle '{bundle_id}' not found")
        return await self.bundle_get(bundle_id)

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

    async def bundle_checked_at_set(self, bundle_id: str, when: datetime) -> None:
        """Mark the pack's Modrinth information as compared with Modrinth at `when`."""
        await self._bundle_key(bundle_id, {"$set": {"modrinth_checked_at": when}})

    async def bundle_manual_refreshed_at_set(self, bundle_id: str, when: datetime) -> None:
        """Mark when "Actualizar desde Modrinth" last ran for the pack."""
        await self._bundle_key(bundle_id, {"$set": {"manual_refreshed_at": when}})

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

    async def build_find_latest(self, bundles: Iterable[ModpackBundle]) -> dict[PydanticObjectId, ModpackBuild]:
        """The build each bundle's `latest_build` points to, by bundle id, in one query; a bundle
        without builds is left out."""
        wanted = [{"bundle_id": b.id, "number": b.latest_build} for b in bundles if b.latest_build is not None]
        if not wanted:
            return {}
        builds = await ModpackBuild.find({"$or": wanted}).to_list()
        return {build.bundle_id: build for build in builds}

    async def build_find_numbers(self, wanted: Iterable[tuple[PydanticObjectId, int]]) -> list[ModpackBuild]:
        """The builds with these (bundle id, number) pairs, in one query; one that does not exist is absent."""
        keys = [{"bundle_id": bundle_id, "number": number} for bundle_id, number in dict.fromkeys(wanted)]
        if not keys:
            return []
        return await ModpackBuild.find({"$or": keys}).to_list()

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

    async def resolution_save(self, resolution: ModpackResolution) -> None:
        """Keep `resolution` as its bundle's latest, replacing the one there was (upsert by bundle)."""
        await ModpackResolution.find_one(ModpackResolution.bundle_id == resolution.bundle_id).upsert(
            {"$set": {"resolved_at": resolution.resolved_at, "request_digest": resolution.request_digest,
                      "lock": resolution.lock.model_dump(mode="json")}},
            on_insert=resolution)

    async def loader_version_get(self, key: str) -> Optional[LoaderVersion]:
        """The kept loader version for "loader|minecraft"; None if it was never asked."""
        return await LoaderVersion.find_one(LoaderVersion.key == key)

    async def loader_version_save(self, record: LoaderVersion) -> None:
        """Keep a loader version, replacing the one with its key."""
        await LoaderVersion.get_pymongo_collection().replace_one(
            {"key": record.key}, record.model_dump(exclude={"id"}), upsert=True)

    async def resolution_get(self, bundle_id: PydanticObjectId) -> Optional[ModpackResolution]:
        """A bundle's latest resolution; None if it was never resolved."""
        return await ModpackResolution.find_one(ModpackResolution.bundle_id == bundle_id)

    async def resolution_find_with_dependency(self, project_id: str) -> list[ModpackResolution]:
        """The saved resolutions that brought this project in as a dependency: an entry of it without
        an origin (one with an origin was asked for by the pack), or a problem about it (a dependency
        without a version has no entry, B4)."""
        return await ModpackResolution.find(
            {"$or": [{"lock.entries": {"$elemMatch": {"project_id": project_id, "origin": None}}},
                     {"lock.problems.project_id": project_id}]}).to_list()

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
