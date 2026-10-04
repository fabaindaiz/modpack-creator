import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional
from src.database import ModpackDatabase, ModrinthDatabase, PterodactylDatabase
from src.library.api.client.modrinth import ModrinthAPI
from src.library.api.client.pterodactyl import PterodactylAPI
from fastapi import HTTPException
from src.library.deduce import Deduction, deduce_target
from src.library.jarmeta import ModMetadata, read_mod_metadata
from src.library.scan import ClaimedFile, Comparison, ListedFile, LocalFile, ScanFile, compare, drift_message, plan_scan
from src.model.modpack import ModpackBundle, PackProject, ProjectOrigin
from src.model.modrinth import ModrinthVersion
from src.model.pterodactyl import BundleForServer, ImportCandidate, ImportResult, ProjectCandidate, PterodactylFile, PterodactylServer, ServerScan
from src.exceptions import *
from src.library.auth.roles import operator_only
from src.model.audit import AuditTarget
from src.model.users import Actor
from src.model.utils import MinecraftLoaders
from src.services.audit import AuditSink

logger = logging.getLogger("modpack.servers")

# A jar from a server is untrusted: it is hashed, never loaded, and not downloaded past this
# size (untrusted-package-is-parsed-never-loaded). The largest common mods are a few tens of MiB.
SCAN_MAX_BYTES = 128 * 1024 * 1024
# Downloads at once. The Client API allows 256 requests a minute, and each jar takes two (the
# signed URL and the download): a large first scan is paced by this and by the panel.
SCAN_CONCURRENCY = 4
SCAN_DIRECTORY = "/mods"
IMPORT_FAILED = ("El modpack quedó creado y vinculado a este servidor, pero la importación de sus mods falló. "
                 "Vuelve a intentarlo con «Importar».")

class PterodactylService():
    def __init__(self,
            pterodactylAPI: PterodactylAPI,
            PterodactylDB: PterodactylDatabase,
            modrinthAPI: ModrinthAPI,
            modrinthDB: ModrinthDatabase,
            modpackDB: ModpackDatabase,
            audit: AuditSink,
            ) -> None:
        self.pterodactylAPI = pterodactylAPI
        self.pterodactylDB = PterodactylDB
        self.modrinthAPI = modrinthAPI
        self.modrinthDB = modrinthDB
        self.modpackDB = modpackDB
        self.audit = audit

    async def server_external(self) -> list[PterodactylServer]:
        """Every server the panel key can see, straight from the panel."""
        return list(await self.pterodactylAPI.server_get_many())

    async def server_find(self) -> list[PterodactylServer]:
        return await self.pterodactylDB.server_find().to_list()

    async def server_get(self, server_id: str) -> PterodactylServer:
        return await self.pterodactylDB.server_get(server_id)

    async def server_create(self, uuid: str, actor: Actor) -> PterodactylServer:
        """
        Create a new Pterodactyl server.
        
        :param uuid: The UUID of the server to create.
        :return: The created PterodactylServer object.
        """
        server = await self.pterodactylAPI.server_get(uuid)
        created = await self.pterodactylDB.server_insert(server)
        await self.audit.record(actor, "server.create", _target(str(created.id)), {"uuid": uuid})
        return created
    
    async def server_update(self, server_id: str, actor: Actor) -> PterodactylServer:
        """
        Update a Pterodactyl server.
        
        :param server_id: The ID of the server to update.
        :return: The updated PterodactylServer object.
        """
        document = await self.pterodactylDB.server_get(server_id)
        server = await self.pterodactylAPI.server_get(document.uuid)
        # The panel does not know our fields: keep them.
        server.bundle_id = document.bundle_id
        saved = await self.pterodactylDB.server_save(server, document.id)
        await self.audit.record(actor, "server.refresh", _target(server_id))
        return saved

    @operator_only
    async def server_link_bundle(self, server_id: str, bundle_id: Optional[str], actor: Actor) -> PterodactylServer:
        """Link a server to the bundle it runs (404 if the bundle does not exist), or unlink it.
        Operators only: the bundle decides what an update writes to the server."""
        if bundle_id is not None:
            await self.modpackDB.bundle_get(bundle_id)
        server = await self.pterodactylDB.server_set_bundle(server_id, bundle_id)
        await self.audit.record(actor, "server.bundle", _target(server_id), {"bundle_id": bundle_id})
        return server
    
    async def server_files(self, server_id: str, directory: str) -> list[PterodactylFile]:
        """
        Get the files of a Pterodactyl server.
        
        :param server_id: The ID of the server to get files from.
        :return: A list of PterodactylFile objects.
        """
        server = await self.pterodactylDB.server_get(server_id)
        return list(await self.pterodactylAPI.server_files(server.identifier, directory))

    # What a server has (i-7985ee-43df2f, option 1). Only reads: listing and downloading.

    async def scan(self, server_id: str, actor: Optional[Actor], directory: str = SCAN_DIRECTORY,
                   full: bool = False) -> ServerScan:
        """
        Scan a directory of a server: which jars it has, and which Modrinth version each one is.

        Lists the directory; downloads and hashes only the jars whose path, size or modification
        time changed since the last scan, or all of them if `full`; identifies them by sha1
        against Modrinth. Writes nothing to the server.

        :param actor: Who asked; None for the scheduler's and the worker's scans, which are not recorded.
        :return: The stored scan.
        """
        server = await self.pterodactylDB.server_get(server_id)
        listing = [ListedFile(path=f"{directory.rstrip('/')}/{f.name}", size=f.size, modified_at=f.modified_at)
                   for f in await self.pterodactylAPI.server_files(server.identifier, directory)
                   if f.is_file and not f.is_symlink and f.name.endswith(".jar")]
        previous = await self.pterodactylDB.scan_latest(server_id, directory)
        kept, to_hash, too_large = plan_scan(listing, previous.files if previous else [], full, SCAN_MAX_BYTES)
        hashed = await self._hash(server.identifier, to_hash)
        # Modrinth may know today a jar it did not know before: unknown ones are asked about again.
        known = [f for f in kept if f.status == "identified"]
        identified = await self._identify([*hashed, *(f for f in kept if f.status == "unknown")])
        files = sorted([*known, *identified, *too_large], key=lambda f: f.path)
        stored = await self.pterodactylDB.scan_insert(ServerScan(
            server_id=server.id, directory=directory, scanned_at=datetime.now(timezone.utc),  # type: ignore[arg-type]
            full=full, files=files))
        if actor is not None:
            await self.audit.record(actor, "server.scan", _target(server_id), {"directory": directory, "full": full})
        return stored

    async def _hash(self, identifier: str, files: list[ListedFile]) -> list[ScanFile]:
        """Download each file and hash its bytes. The bytes are hashed, never opened or loaded."""
        limit = asyncio.Semaphore(SCAN_CONCURRENCY)

        async def one(file: ListedFile) -> ScanFile:
            async with limit:
                data = await self.pterodactylAPI.server_files_download(identifier, file.path)
            if len(data) > SCAN_MAX_BYTES:
                return ScanFile(**file.model_dump(), status="too_large")
            return ScanFile(**file.model_dump(), status="unknown",
                            sha1=hashlib.sha1(data).hexdigest(), sha512=hashlib.sha512(data).hexdigest())

        return list(await asyncio.gather(*[one(f) for f in files]))

    async def _identify(self, files: list[ScanFile]) -> list[ScanFile]:
        """Ask Modrinth which version each sha1 belongs to, and store those versions in the catalog.
        A version counts only if one of its files has exactly that sha1."""
        hashes = sorted({f.sha1 for f in files if f.sha1})
        found: dict[str, ModrinthVersion] = await self.modrinthAPI.files_hash_post_many(hashes, "sha1") if hashes else {}
        result = []
        for file in files:
            version = found.get(file.sha1 or "")
            if version and any(f.hashes.sha1 == file.sha1 for f in version.files):
                file = file.model_copy(update={"status": "identified", "project_id": version.project_id,
                                               "version_id": version.version_id})
            result.append(file)
        if found:
            await self.modrinthDB.version_upsert_many(found.values())
        return result

    async def scan_latest(self, server_id: str, directory: str = SCAN_DIRECTORY) -> ServerScan:
        scan = await self.pterodactylDB.scan_latest(server_id, directory)
        if scan is None:
            raise HTTP_404_NOT_FOUND(f"Server '{server_id}' has no scan of {directory}")
        return scan

    async def scan_find(self, server_id: str) -> list[ServerScan]:
        return await self.pterodactylDB.scan_find(server_id)

    async def drift(self, server_id: str) -> Optional[str]:
        """How the latest scan compares with the server's build, as a message; None if the server
        is not linked to a bundle with a build."""
        server = await self.pterodactylDB.server_get(server_id)
        if server.bundle_id is None:
            return None
        bundle = await self.modpackDB.bundle_get(server.bundle_id)
        number = bundle.recommended_build or bundle.latest_build
        if number is None:
            return None
        comparison = await self.compare(server_id, server.bundle_id, number)
        return drift_message(server.name, bundle.name, number, comparison)

    async def compare(self, server_id: str, bundle_id: Optional[str] = None, build: Optional[int] = None,
                      directory: str = SCAN_DIRECTORY) -> Comparison:
        """
        Compare a server's latest scan with a build of a bundle (i-7985ee-4364a3): what matches,
        what has another version, what is missing, extra or unknown.

        :param bundle_id: By default, the bundle the server is linked to; 409 if it has none.
        :param build: The build number; by default the recommended one, else the latest.
        """
        if bundle_id is None:
            bundle_id = (await self.pterodactylDB.server_get(server_id)).bundle_id
            if bundle_id is None:
                raise HTTP_409_CONFLICT(f"Server '{server_id}' is not linked to a bundle: name one, or link it")
        bundle = await self.modpackDB.bundle_get(bundle_id)
        number = build if build is not None else (bundle.recommended_build or bundle.latest_build)
        if number is None:
            raise HTTP_409_CONFLICT(f"Bundle '{bundle_id}' has no build to compare with")
        frozen = await self.modpackDB.build_get(bundle_id, number)
        scan = await self.scan_latest(server_id, directory)
        return compare(scan.files, frozen.lock.entries, frozen.local_files, bundle.claimed_files)

    # Importing a server (i-7985ee-17745b, d-7985ee-c8a15d): its jars that the bundle does not
    # explain are brought into the bundle. Only reads the server.

    async def _import_context(self, server_id: str) -> tuple[PterodactylServer, ModpackBundle, ServerScan]:
        server = await self.pterodactylDB.server_get(server_id)
        if server.bundle_id is None:
            raise HTTP_409_CONFLICT(f"Server '{server_id}' is not linked to a bundle: link it before importing")
        bundle = await self.modpackDB.bundle_get(server.bundle_id)
        return server, bundle, await self.scan_latest(server_id)

    @operator_only
    async def import_server(self, server_id: str, actor: Actor) -> ImportResult:
        """
        Bring into the server's bundle every jar of its latest scan that the bundle does not explain:
        identified by hash, the project joins as the admin's; unknown, its metadata is read as data
        and Modrinth is searched for candidates, which wait for the admin; without candidates, the
        jar becomes a local file of the bundle. Running it again adds nothing new.
        """
        server, bundle, scan = await self._import_context(server_id)
        in_bundle = {p.project_id for p in bundle.projects}
        local = {f.sha512 for f in bundle.local_files}
        claimed = {c.sha512 for c in bundle.claimed_files}
        result = ImportResult(added=[], candidates=[], local=[], too_large=[])
        for file in scan.files:
            if file.status == "too_large":
                result.too_large.append(file.path)
            elif file.status == "identified" and file.project_id and file.project_id not in in_bundle:
                in_bundle.add(file.project_id)
                result.added.append(file.project_id)
            elif file.status == "unknown" and file.sha512 and file.sha512 not in local | claimed:
                data = await self.pterodactylAPI.server_files_download(server.identifier, file.path)
                metadata = read_mod_metadata(data) if len(data) <= SCAN_MAX_BYTES else None
                candidates = await self._candidates(metadata) if metadata else []
                if candidates:
                    result.candidates.append(ImportCandidate(path=file.path, sha512=file.sha512,
                                                             metadata=metadata, candidates=candidates))
                else:
                    await self.modpackDB.bundle_local_file_add(str(bundle.id), _local(file))
                    result.local.append(file.path)
        if result.added:
            await self._add_projects(str(bundle.id), result.added)
        await self.audit.record(actor, "server.import", _target(server_id), {
            "bundle_id": str(bundle.id), "added": result.added, "local": result.local,
            "candidates": [c.path for c in result.candidates]})
        return result

    async def _candidates(self, metadata: ModMetadata) -> list[ProjectCandidate]:
        """Modrinth projects a jar's metadata may be: its mod id as a slug, then a search by name.
        Never accepted here: the metadata named the wrong project in 1 of 9 real jars."""
        found: dict[str, ProjectCandidate] = {}
        try:
            project = await self.modrinthAPI.project_get(metadata.mod_id)
            if project.project_type.value == "mod":
                found[project.project_id] = ProjectCandidate(project_id=project.project_id, slug=project.slug,
                                                             title=project.title, via="slug")
        except HTTPException:
            pass
        for hit in await self.modrinthAPI.search(metadata.name or metadata.mod_id, [["project_type:mod"]], limit=3):
            found.setdefault(hit["project_id"], ProjectCandidate(project_id=hit["project_id"], slug=hit["slug"],
                                                                 title=hit["title"], via="search"))
        return list(found.values())

    async def _add_projects(self, bundle_id: str, project_ids: list[str]) -> None:
        projects = await self.modrinthDB.project_upsert_many(await self.modrinthAPI.project_get_many(project_ids))
        await self.modpackDB.bundle_projects_add(
            bundle_id, [PackProject(project_id=p.project_id, origin=ProjectOrigin.admin) for p in projects])

    # A new bundle for a server without one (panel usability §2): deduced from the latest scan.

    async def deduction(self, server_id: str) -> Optional[Deduction]:
        """The loader and Minecraft version the identified jars of the latest scan point to, read
        from the catalog; None if the server has no scan. A jar whose version the catalog does not
        have does not vote."""
        scan = await self.pterodactylDB.scan_latest(server_id, SCAN_DIRECTORY)
        if scan is None:
            return None
        version_ids = [f.version_id for f in scan.files if f.status == "identified" and f.version_id]
        versions = {v.version_id: v for v in await self.modrinthDB.version_find_by_version_ids(sorted(set(version_ids)))}
        return deduce_target([(versions[i].loaders, versions[i].game_versions) for i in version_ids if i in versions])

    @operator_only
    async def bundle_for_server(self, server_id: str, name: str, version: str, loader: MinecraftLoaders,
                                import_mods: bool, actor: Actor) -> BundleForServer:
        """
        Create a bundle for a server without one, link it and, if `import_mods`, import the server
        into it, each step with its audit event. Operators only, since linking and importing are.
        409 if the server already has a bundle, before anything is created. If importing fails,
        the bundle stays created and linked (an empty linked bundle, never a touched server: this
        only reads the panel) and the answer says so.
        """
        server = await self.pterodactylDB.server_get(server_id)
        if server.bundle_id is not None:
            raise HTTP_409_CONFLICT("Este servidor ya tiene modpack: vuelve a cargar la página")
        bundle = await self.modpackDB.bundle_insert(ModpackBundle(name=name, description="", version=version, loader=loader))
        await self.audit.record(actor, "bundle.create", AuditTarget(kind="bundle", id=str(bundle.id)))
        await self.server_link_bundle(server_id, str(bundle.id), actor)
        imported, import_error = None, None
        if import_mods:
            try:
                imported = await self.import_server(server_id, actor)
            except Exception:
                logger.warning("import after creating a bundle for a server failed", exc_info=True,
                               extra={"server_id": server_id, "bundle_id": str(bundle.id)})
                import_error = IMPORT_FAILED
        return BundleForServer(bundle=bundle, imported=imported, import_error=import_error)

    def _scanned(self, scan: ServerScan, path: str) -> ScanFile:
        file = next((f for f in scan.files if f.path == path), None)
        if file is None or file.sha512 is None:
            raise HTTP_404_NOT_FOUND(f"The latest scan has no hashed file {path}")
        return file

    @operator_only
    async def import_confirm(self, server_id: str, path: str, project_id: str, actor: Actor) -> list[PackProject]:
        """The admin confirms that a jar is a Modrinth project: the project joins the bundle as the
        admin's, and the jar is claimed by it, to be replaced by the build's version."""
        _, bundle, scan = await self._import_context(server_id)
        file = self._scanned(scan, path)
        await self._add_projects(str(bundle.id), [project_id])
        await self.modpackDB.bundle_claim_add(str(bundle.id), ClaimedFile(sha512=file.sha512 or "", project_id=project_id))
        await self.audit.record(actor, "server.import.confirm", _target(server_id),
                                {"bundle_id": str(bundle.id), "path": path, "project_id": project_id})
        return (await self.modpackDB.bundle_get(str(bundle.id))).projects

    @operator_only
    async def import_local(self, server_id: str, path: str, actor: Actor) -> list[LocalFile]:
        """The admin keeps a jar as a local file of the bundle, untouched by updates."""
        _, bundle, scan = await self._import_context(server_id)
        files = await self.modpackDB.bundle_local_file_add(str(bundle.id), _local(self._scanned(scan, path)))
        await self.audit.record(actor, "server.import.local", _target(server_id),
                                {"bundle_id": str(bundle.id), "path": path})
        return files

def _target(server_id: str) -> AuditTarget:
    return AuditTarget(kind="server", id=server_id)

def _local(file: ScanFile) -> LocalFile:
    return LocalFile(filename=file.path.rsplit("/", 1)[-1], sha512=file.sha512 or "", sha1=file.sha1 or "", size=file.size)
