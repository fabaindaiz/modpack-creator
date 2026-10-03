import re
from datetime import datetime, timezone
from typing import Optional
from src.database import ModpackDatabase, ModrinthDatabase
from src.library.api.client.loaders import LoaderMetaAPI
from src.library.api.client.modrinth import ModrinthAPI
from src.library.mrpack import mrpack_index, write_mrpack
from src.services.jobs import NotificationSink
from src.library.resolution import Candidate, Catalog, Lock, ResolveRequest, Target, merge_replacements, resolve
from src.model.modpack import ModpackBuild, ModpackBundle, ModpackBundlePatch, ModpackReplacement, PackProject, ProjectOrigin
from src.model.modrinth import ModrinthProject, ModrinthVersion
from src.model.audit import AuditTarget
from src.model.users import Actor
from src.services.audit import AuditSink
from src.exceptions import *

# Each round fetches what the lock needs; a dependency chain deeper than this is reported as an
# incomplete lock instead of fetching forever.
RESOLVE_ROUNDS = 32
# What a Modrinth project id looks like; anything else is not a key to store.
MODRINTH_ID = re.compile(r"[A-Za-z0-9_-]+")

def candidate(version: ModrinthVersion) -> Candidate:
    return Candidate.from_modrinth({**version.model_dump(mode="json"), "id": version.version_id})

class ModpackService():
    def __init__(self,
            modpackDB: ModpackDatabase,
            modrinthDB: ModrinthDatabase,
            modrinthAPI: ModrinthAPI,
            loaderAPI: LoaderMetaAPI,
            audit: AuditSink,
            notifier: Optional[NotificationSink] = None,
            ) -> None:
        self.modpackDB = modpackDB
        self.modrinthDB = modrinthDB
        self.modrinthAPI = modrinthAPI
        self.loaderAPI = loaderAPI
        self.audit = audit
        self.notifier = notifier

    async def bundle_find(self) -> list[ModpackBundle]:
        return await self.modpackDB.bundle_find().to_list()

    async def bundle_create(self, bundle: ModpackBundle, actor: Actor) -> ModpackBundle:
        created = await self.modpackDB.bundle_insert(bundle=bundle)
        await self.audit.record(actor, "bundle.create", _bundle(str(created.id)))
        return created

    async def bundle_get(self, bundle_id: str) -> ModpackBundle:
        return await self.modpackDB.bundle_get(bundle_id=bundle_id)

    async def bundle_update(self, bundle_id: str, patch: ModpackBundlePatch, actor: Actor) -> ModpackBundle:
        updated = await self.modpackDB.bundle_update(bundle_id=bundle_id, patch=patch)
        await self.audit.record(actor, "bundle.update", _bundle(bundle_id))
        return updated

    async def decision_set(self, bundle_id: str, project_id: str, server: bool, actor: Actor) -> ModpackBundle:
        """Decide whether one mod goes on the server; only that decision changes, atomically.
        422 for an id that is not a Modrinth project id: it would become a nested key."""
        if not MODRINTH_ID.fullmatch(project_id):
            raise HTTP_422_UNPROCESSABLE_ENTITY(f"'{project_id}' is not a Modrinth project id")
        updated = await self.modpackDB.bundle_decision_set(bundle_id, project_id, server)
        await self.audit.record(actor, "bundle.decision", _bundle(bundle_id),
                                {"project_id": project_id, "server": server})
        return updated

    async def decisions_set(self, bundle_id: str, decisions: dict[str, bool], actor: Actor) -> ModpackBundle:
        """Decide several mods at once, in one atomic write. 422 if there is none or any id is not a
        Modrinth project id: every id is checked first, so nothing is written for a bad batch."""
        if not decisions:
            raise HTTP_422_UNPROCESSABLE_ENTITY("There is no decision to save")
        for project_id in decisions:
            if not MODRINTH_ID.fullmatch(project_id):
                raise HTTP_422_UNPROCESSABLE_ENTITY(f"'{project_id}' is not a Modrinth project id")
        updated = await self.modpackDB.bundle_decisions_set(bundle_id, decisions)
        await self.audit.record(actor, "bundle.decision", _bundle(bundle_id), {"decisions": decisions})
        return updated

    # Content (i-7985ee-063a3a).

    async def project_find(self, bundle_id: str) -> list[PackProject]:
        return (await self.modpackDB.bundle_get(bundle_id)).projects

    async def project_add(self, bundle_id: str, refs: list[str], origin: ProjectOrigin, actor: Actor) -> list[PackProject]:
        """
        Add Modrinth projects to a bundle, by slug or id, marked with their origin.

        All or nothing: a reference Modrinth does not know refuses the whole request.

        :param refs: Modrinth slugs or project ids.
        :param origin: Whether the pack brings them or the admin adds them.
        :return: The bundle's projects afterwards.
        """
        await self.modpackDB.bundle_get(bundle_id)
        projects = await self._modrinth_projects(refs)
        added = await self.modpackDB.bundle_projects_add(
            bundle_id, [PackProject(project_id=p.project_id, origin=origin) for p in projects])
        await self.audit.record(actor, "bundle.project.add", _bundle(bundle_id),
                                {"refs": list(refs), "origin": origin.value})
        return added

    async def pack_replace(self, bundle_id: str, refs: list[str], actor: Actor) -> list[PackProject]:
        """
        Replace what the pack brings with `refs`; what the admin added stays (d-7985ee-b15dd4).

        :return: The bundle's projects afterwards.
        """
        await self.modpackDB.bundle_get(bundle_id)
        projects = await self._modrinth_projects(refs)
        replaced = await self.modpackDB.bundle_pack_replace(
            bundle_id, [PackProject(project_id=p.project_id, origin=ProjectOrigin.pack) for p in projects])
        await self.audit.record(actor, "bundle.project.replace", _bundle(bundle_id), {"refs": list(refs)})
        return replaced

    async def project_remove(self, bundle_id: str, project_id: str, actor: Actor) -> list[PackProject]:
        remaining = await self.modpackDB.bundle_project_remove(bundle_id, project_id)
        await self.audit.record(actor, "bundle.project.remove", _bundle(bundle_id), {"project_id": project_id})
        return remaining

    async def _modrinth_projects(self, refs: list[str]) -> list[ModrinthProject]:
        """The Modrinth projects these slugs or ids name, stored in the catalog; 404 naming any
        that Modrinth does not know."""
        found = list(await self.modrinthAPI.project_get_many(list(refs))) if refs else []
        known = {key for p in found for key in (p.project_id, p.slug)}
        missing = [ref for ref in refs if ref not in known]
        if missing:
            raise HTTP_404_NOT_FOUND(f"Modrinth has no project {', '.join(missing)}")
        return await self.modrinthDB.project_upsert_many(found)

    # Resolution (i-7985ee-7f735a).

    async def resolve(self, bundle_id: str) -> Lock:
        """
        Resolve a bundle into a lock, fetching from Modrinth what it needs.

        The lock is computed, not stored: build_create freezes it. Every project and version
        fetched is stored in the catalog.

        :param bundle_id: The ID of the bundle.
        :return: The lock; `complete` is False only if the dependency chain ran past RESOLVE_ROUNDS.
        """
        return await self._resolve(await self._request(await self.modpackDB.bundle_get(bundle_id)))

    async def _request(self, bundle: ModpackBundle) -> ResolveRequest:
        global_rules = {r.project_id: r.replacement for r in await self.modpackDB.replacement_find()}
        return ResolveRequest(
            target=Target(game_version=bundle.version, loader=str(bundle.loader.value),
                          channels=frozenset(bundle.channels)),
            projects=[p.project_id for p in bundle.projects],
            origins={p.project_id: p.origin.value for p in bundle.projects},
            pins=bundle.pins, server_decisions=bundle.server_decisions,
            replacements=merge_replacements(global_rules, bundle.replacements))

    async def _resolve(self, request: ResolveRequest) -> Lock:
        catalog = Catalog()
        for _ in range(RESOLVE_ROUNDS):
            lock = resolve(request, catalog)
            if lock.complete:
                return lock
            fetched: list[ModrinthVersion] = []
            await self._fetch_types(catalog, sorted(lock.needs_projects | lock.needs_types))
            for project_id in sorted(lock.needs_projects):
                loaders = sorted(request.target.loaders(catalog.types.get(project_id, "mod")))
                fetched += await self.modrinthAPI.project_versions(project_id, loaders, [request.target.game_version])
                catalog.by_project.setdefault(project_id, [])
            if lock.needs_versions:
                asked = sorted(lock.needs_versions)
                versions = list(await self.modrinthAPI.version_get_many(asked))
                catalog.gone |= set(asked) - {v.version_id for v in versions}
                fetched += versions
            for version in fetched:
                catalog.add(candidate(version))
            if fetched:
                await self.modrinthDB.version_upsert_many(fetched)
        return lock

    async def _fetch_types(self, catalog: Catalog, project_ids: list[str]) -> None:
        """Fetch the projects whose type resolution needs, and store them in the catalog. One
        Modrinth does not return is typed unknown, which resolution reports."""
        if not project_ids:
            return
        projects = list(await self.modrinthAPI.project_get_many(project_ids))
        for project in projects:
            catalog.types[project.project_id] = project.project_type.value
        for project_id in set(project_ids) - {p.project_id for p in projects}:
            catalog.types[project_id] = "unknown"
        if projects:
            await self.modrinthDB.project_upsert_many(projects)

    # Builds (i-7985ee-098611).

    async def build_create(self, bundle_id: str, actor: Actor) -> ModpackBuild:
        """
        Resolve the bundle and freeze the lock as its next build; latest moves to it.

        Refused with 409 when the lock is not complete, has problems, or has mods whose place on
        the server waits for the admin: a build is something that can be deployed as it is.

        :return: The new build.
        """
        bundle = await self.modpackDB.bundle_get(bundle_id)
        request = await self._request(bundle)
        lock = await self._resolve(request)
        if not lock.complete or lock.problems or lock.undecided:
            reasons = [p.detail for p in lock.problems]
            reasons += [f"{e.project_id}: falta decidir si va en el servidor" for e in lock.undecided]
            if not lock.complete:
                reasons.append("la resolución no terminó")
            raise HTTP_409_CONFLICT("No se puede congelar este lock: " + "; ".join(reasons))
        loader_version = bundle.loader_version or await self.loaderAPI.loader_version(
            str(bundle.loader.value), bundle.version)
        builds = await self.modpackDB.build_find(bundle_id)
        number = (builds[0].number if builds else 0) + 1
        build = await self.modpackDB.build_insert(ModpackBuild(
            bundle_id=bundle.id, number=number, created_at=datetime.now(timezone.utc),  # type: ignore[arg-type]
            loader_version=loader_version, local_files=bundle.local_files, request=request, lock=lock))
        await self.modpackDB.bundle_latest_build(bundle_id, number)
        await self.audit.record(actor, "build.create", _bundle(bundle_id), {"number": number})
        if self.notifier is not None:
            await self.notifier.notify(f"📦 {bundle.name}: build {number} creado, {len(lock.entries)} archivos.")
        return build

    async def build_find(self, bundle_id: str) -> list[ModpackBuild]:
        return await self.modpackDB.build_find(bundle_id)

    async def latest_builds(self) -> list[tuple[ModpackBundle, Optional[ModpackBuild]]]:
        """Every bundle with its newest build, or None if it has none yet."""
        bundles = await self.modpackDB.bundle_find().to_list()
        latest = await self.modpackDB.build_find_latest(bundles)
        return [(bundle, latest.get(bundle.id) if bundle.id is not None else None) for bundle in bundles]

    async def build_get(self, bundle_id: str, number: int) -> ModpackBuild:
        return await self.modpackDB.build_get(bundle_id, number)

    async def build_mrpack(self, bundle_id: str, number: int) -> tuple[str, bytes]:
        """
        A build as a .mrpack for players (i-7985ee-7352b3): the same files the server gets, by
        their Modrinth URL, validated against the format before it is returned.

        :return: A file name and the archive. 409 if the build has no loader version, or if the
            index would break the format.
        """
        bundle = await self.modpackDB.bundle_get(bundle_id)
        build = await self.modpackDB.build_get(bundle_id, number)
        if build.loader_version is None:
            raise HTTP_409_CONFLICT(f"Build {number} has no {build.request.target.loader} version: set the "
                                    "bundle's loader_version and create a new build")
        index = mrpack_index(name=bundle.name, version_id=str(number), summary=bundle.description,
                             game_version=build.request.target.game_version, loader=build.request.target.loader,
                             loader_version=build.loader_version, entries=build.lock.entries)
        try:
            data = write_mrpack(index)
        except ValueError as error:
            raise HTTP_409_CONFLICT(f"Build {number} does not make a valid .mrpack: {error}") from error
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", bundle.name).strip("-") or "modpack"
        return f"{safe}-{number}.mrpack", data

    async def build_recommend(self, bundle_id: str, number: int, actor: Actor) -> ModpackBundle:
        """Point recommended at an existing build; going back is pointing at an older one."""
        await self.modpackDB.build_get(bundle_id, number)
        bundle = await self.modpackDB.bundle_recommend_build(bundle_id, number)
        await self.audit.record(actor, "build.recommend", _bundle(bundle_id), {"number": number})
        return bundle

    # Global replacement rules (d-7985ee-86fffa).

    async def replacement_find(self) -> list[ModpackReplacement]:
        return await self.modpackDB.replacement_find()

    async def replacement_set(self, project_id: str, replacement: Optional[str], actor: Actor) -> ModpackReplacement:
        rule = await self.modpackDB.replacement_set(ModpackReplacement(project_id=project_id, replacement=replacement))
        await self.audit.record(actor, "replacement.set", _replacement(project_id), {"replacement": replacement})
        return rule

    async def replacement_delete(self, project_id: str, actor: Actor) -> bool:
        if not await self.modpackDB.replacement_delete(project_id):
            raise HTTP_404_NOT_FOUND(f"No replacement rule for project '{project_id}'")
        await self.audit.record(actor, "replacement.delete", _replacement(project_id))
        return True

def _bundle(bundle_id: str) -> AuditTarget:
    return AuditTarget(kind="bundle", id=bundle_id)

def _replacement(project_id: str) -> AuditTarget:
    return AuditTarget(kind="replacement", id=project_id)
