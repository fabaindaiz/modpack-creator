"""Import a modpack (spec docs/superpowers/specs/2026-10-09-importar-modpacks-design.md).

A .mrpack someone uploads, or one published on Modrinth, becomes a new pack or replaces the content
of one (what the admin added stays). Its files are identified by hash: the catalog first, then one
Modrinth call for every hash it lacks, and the projects in one more. What runs is recorded as with a
server import (d-7985ee-58801d): env.server seeds the server decision and a beta or alpha outside the
pack's channels the channel exception, only where nothing is set. Each mod is then pinned to the
pack's own version, its «versión del pack» origin, validated and marked if it does not pass (O2,
`ModpackService.pins_from_pack`): never over another choice, never going back.
"""
import asyncio
import logging
import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import IO, Any, Optional
from urllib.parse import urlparse
from fastapi import HTTPException
from src.database import ModpackDatabase, ModrinthDatabase
from src.library.api.client.download import Downloader, TooLarge
from src.library.api.client.modrinth import ModrinthAPI
from src.library.import_uploads import UploadStore, read_upload, remove_quietly
from src.library.mrpack_import import IndexFile, MrpackContent, MrpackError, MrpackLimits, OverrideJar, read_mrpack
from src.library.prism_import import PrismLimits
from src.library.cf_import import CfContent, CfFile, read_cf_zip
from src.library.api.client.curseforge import CurseForgeAPI
from src.database.jobs import JobDatabase
from src.model.jobs import Job
from src.services.jobs import Handler, JobContext, JobFailed, LeaseLost
from src.library.resolution import EXCEPTION_CHANNELS
from src.model.audit import AuditTarget
from src.model.modpack import ModpackBundle
from src.model.modrinth import ModrinthProject, ModrinthVersion
from src.model.modrinth.version import ModrinthVersionFile
from src.model.users import Actor
from src.model.utils import MinecraftLoaders
from src.services.audit import AuditSink
from src.library.utils import MODRINTH_ID
from src.services.modpack import ModpackService
from src.exceptions import HTTP_404_NOT_FOUND, HTTP_409_CONFLICT, HTTP_422_UNPROCESSABLE_ENTITY

logger = logging.getLogger("modpack.import")
LIMITS = MrpackLimits()
NO_CURSEFORGE_KEY = "Esta instalación no tiene clave de CurseForge: exporta el pack como .mrpack desde Prism."
CF_CODE_FAILED = "CurseForge no respondió al código; expórtalo como .zip"
# A CurseForge profile code: letters and digits (8 in the ones seen; the format is not documented).
CF_CODE = re.compile(r"[A-Za-z0-9]{4,32}")
PRISM_LIMITS = PrismLimits()
UPLOAD_GONE = "El archivo subido ya no está en el servidor (se reinició antes de leerlo): súbelo de nuevo"
# A stored upload no active job holds is removed once untouched this long: a request between storing
# its file and queuing its job is never caught.
UPLOAD_GRACE = timedelta(minutes=30)
# A published modpack's file is only fetched from Modrinth's CDN.
DOWNLOAD_HOST = "cdn.modrinth.com"
MODRINTH_URL = re.compile(r"/(?:modpack|project)/([A-Za-z0-9_.-]+)(?:/version/([A-Za-z0-9_.+-]+))?/?")
SERVER_ENV = {"required": True, "unsupported": False}


@dataclass
class ImportReport:
    bundle_id: str
    created: bool
    # What was imported: the pack's name and version, or the file's.
    source: str
    projects: list[str] = field(default_factory=list)
    # Paths of files Modrinth does not have: to be put on the server by hand (D3).
    unknown: list[str] = field(default_factory=list)
    # Override files not imported, counted by folder (D4).
    overrides: dict[str, int] = field(default_factory=dict)
    decisions: dict[str, bool] = field(default_factory=dict)
    channel_exceptions: dict[str, str] = field(default_factory=dict)
    # Jars a Prism instance had turned off (`.jar.disabled`, T6/P2): they come in «Desactivado».
    disabled: int = 0
    # Files of a CurseForge pack Modrinth does not have (C2, C3): the manifest's ids, nothing of CurseForge's.
    curseforge_only: list[dict[str, int]] = field(default_factory=list)
    # Mods pinned to the pack's own version (O2); of them, those Modrinth does not mark for the pack
    # (project id -> why), used anyway; and those where it is only offered on the card (another
    # choice was in use, or it goes back). Reports saved before O2 load without them.
    pinned: list[str] = field(default_factory=list)
    outside: dict[str, str] = field(default_factory=dict)
    offered: list[str] = field(default_factory=list)
    # Those whose pack version was not written at all: every try met another write of the mod's record
    # (`PackPins.busy`). Reports saved before load without it.
    busy: list[str] = field(default_factory=list)


def _import_key(actor: Actor) -> str:
    """One import at a time per person; the identity is iss + sub (AGENTS.md)."""
    return f"import:{actor.iss}|{actor.sub}"


def _report_of(job: Job) -> Optional[ImportReport]:
    saved = job.checkpoint.get("import")
    return ImportReport(**saved) if saved else None


class ModpackImportService():
    def __init__(self,
            modpackService: ModpackService,
            modpackDB: ModpackDatabase,
            modrinthDB: ModrinthDatabase,
            modrinthAPI: ModrinthAPI,
            downloader: Downloader,
            audit: AuditSink,
            jobDB: Optional[JobDatabase] = None,
            curseforge: Optional[CurseForgeAPI] = None,
            uploads: Optional[UploadStore] = None,
            ) -> None:
        self.jobDB = jobDB or JobDatabase()
        # Uploaded files waiting for their import job (O6).
        self.uploads = uploads or UploadStore()
        # Off without the owner's key (C1): nothing calls CurseForge then.
        self.curseforge = curseforge
        self.modpackService = modpackService
        self.modpackDB = modpackDB
        self.modrinthDB = modrinthDB
        self.modrinthAPI = modrinthAPI
        self.downloader = downloader
        self.audit = audit

    async def import_upload(self, file: IO[bytes], filename: str, actor: Actor, into: Optional[str] = None) -> Job:
        """
        Import an uploaded .mrpack, Prism instance zip (T6) or CurseForge zip (T5) in the person's
        import job (O6, owner 2026-10-21): the request only stores the file, in a thread, and queues
        the job, which reads it, tells which of the three it is, and identifies it on Modrinth. One
        at a time: asking again gives the running one, and the second file is not kept. A file that
        is not a pack, a pack for another Minecraft version or loader than `into`, or a CurseForge
        zip without the owner's key (C1) fail the job, naming why.
        """
        name = await asyncio.to_thread(self.uploads.save, file)
        try:
            job, new = await self._job(actor, {"upload": name, "into": into})
            if new:
                source = filename[:200]
                await self.audit.record(actor, "bundle.import.request", _target(into),
                                        {"source": source, "job_id": str(job.id)})
        except BaseException:
            await asyncio.to_thread(self.uploads.remove, name)
            raise
        if not new:
            await asyncio.to_thread(self.uploads.remove, name)
        return job

    async def uploads_sweep(self, now: datetime) -> list[str]:
        """Remove the stored uploads no active import holds (a restart, a job that died), past a
        grace that covers a request between storing its file and queuing its job."""
        held = {str(j.payload.get("upload")) for j in await self.jobDB.active_of_kind("modpack_import")}
        return await asyncio.to_thread(self.uploads.sweep, held, now, UPLOAD_GRACE)

    async def run_sweeper(self, stop: asyncio.Event, every: float = 3600.0) -> None:
        """Call uploads_sweep now and every `every` seconds until `stop` is set."""
        while not stop.is_set():
            try:
                removed = await self.uploads_sweep(datetime.now(timezone.utc))
                if removed:
                    logger.info("removed %d import uploads no job holds", len(removed))
            except Exception:
                logger.exception("import upload sweep error")
            try:
                await asyncio.wait_for(stop.wait(), every)
            except asyncio.TimeoutError:
                pass

    @property
    def curseforge_enabled(self) -> bool:
        return self.curseforge is not None and self.curseforge.enabled

    async def import_curseforge_code(self, code: str, actor: Actor, into: Optional[str] = None) -> Job:
        """
        Import a CurseForge profile code (C5, owner's decision: an undocumented endpoint, behind the
        key). The job downloads the profile's zip; if CurseForge does not answer, it says so and
        nothing else is touched. A code lives 7 days.
        """
        self._need_curseforge()
        code = code.strip()
        if not CF_CODE.fullmatch(code):
            raise HTTP_422_UNPROCESSABLE_ENTITY("Escribe el código de perfil de CurseForge")
        job, new = await self._job(actor, {"cf_code": code, "into": into})
        if new:
            await self.audit.record(actor, "bundle.import.request", _target(into),
                                    {"source": f"curseforge:{code}", "job_id": str(job.id)})
        return job

    def _need_curseforge(self) -> None:
        if not self.curseforge_enabled:
            raise HTTP_422_UNPROCESSABLE_ENTITY(NO_CURSEFORGE_KEY)

    async def import_modrinth(self, ref: str, actor: Actor, into: Optional[str] = None) -> Job:
        """
        Import a modpack published on Modrinth (by URL, slug or id) in the person's import job. A
        reference that is not Modrinth's is a 422 now, with no job.
        """
        _parse_ref(ref)
        job, new = await self._job(actor, {"ref": ref, "into": into})
        if new:
            await self.audit.record(actor, "bundle.import.request", _target(into), {"source": ref, "job_id": str(job.id)})
        return job

    async def import_status(self, job_id: str) -> tuple[Job, Optional[ImportReport]]:
        """An import job and, once it is done, its report."""
        job = await self.jobDB.get(job_id)
        if job.kind != "modpack_import":
            raise HTTP_404_NOT_FOUND(f"Job '{job_id}' is not an import")
        return job, _report_of(job)

    async def last_import(self, actor: Actor) -> Optional[tuple[Job, Optional[ImportReport]]]:
        """The person's import in progress or, if none, their last one, with its report once done:
        the import page shows it, so a reload does not lose it (O9 user-walk, A2)."""
        job = await self.jobDB.last_for_key(_import_key(actor))
        return (job, _report_of(job)) if job is not None else None

    async def _job(self, actor: Actor, payload: dict[str, Any]) -> tuple[Job, bool]:
        """The person's import job: a new one (True), or the one already queued or running (False).
        Into a pack, it names it as `bundle_id`, as the pack's other jobs (O9 user-walk, A1)."""
        key = _import_key(actor)
        if payload.get("into") is not None:
            payload = {**payload, "bundle_id": payload["into"]}
        try:
            return await self.jobDB.enqueue("modpack_import", key=key, payload=payload, requested_by=actor.ref(),
                                            max_attempts=1), True
        except HTTP_409_CONFLICT:
            active = await self.jobDB.active_for(key)
            if active is None:
                raise
            return active, False

    def handlers(self) -> dict[str, Handler]:
        return {"modpack_import": self._run}

    async def _run(self, job: Job, context: JobContext) -> None:
        asked = job.requested_by
        if asked is None:
            raise JobFailed("Una importación sin quién la pidió no se hace: no quedaría en la auditoría")
        actor = Actor(iss=asked.iss, sub=asked.sub, name=asked.name, groups=frozenset(), is_operator=False)
        payload = job.payload

        async def run() -> dict[str, Any]:
            try:
                if "upload" in payload:
                    report = await self._run_upload(str(payload["upload"]), actor, payload.get("into"), context)
                elif "ref" in payload:
                    await context.progress("Buscando el modpack en Modrinth", 0, 1)
                    report = await self.run_modrinth(payload["ref"], actor, payload.get("into"))
                elif "cf_code" in payload:
                    await context.progress("Bajando el perfil de CurseForge", 0, 1)
                    report = await self.run_curseforge_code(payload["cf_code"], actor, payload.get("into"))
                elif "cf" in payload:
                    await context.progress("Identificando los archivos en Modrinth", 0, 1)
                    report = await self.run_curseforge(_cf_content(payload["cf"]), actor, payload.get("into"),
                                                       payload["source"])
                else:
                    await context.progress("Identificando los archivos en Modrinth", 0, 1)
                    report = await self.run_content(_content(payload["content"]), actor, payload.get("into"),
                                                    payload["source"])
            except HTTPException as error:
                # Refused for a reason the person reads (not on Modrinth, altered file): no retry.
                raise JobFailed(str(error.detail)) from error
            return asdict(report)

        upload = payload.get("upload")
        try:
            await context.step("import", run)
        except LeaseLost:
            raise  # another worker holds the job now, and reads the file
        except BaseException:
            await self._drop_upload(upload)
            raise
        await self._drop_upload(upload)

    async def _drop_upload(self, name: Any) -> None:
        """The job ended, done or failed (it has one attempt): its stored file goes."""
        if isinstance(name, str):
            await asyncio.to_thread(remove_quietly, self.uploads, name)

    async def _run_upload(self, name: str, actor: Actor, into: Optional[str], context: JobContext) -> ImportReport:
        """The job's part of an uploaded file: read it in a thread (it hashes every jar), then
        identify it as an .mrpack, a Prism instance or a CurseForge zip would be."""
        try:
            path = self.uploads.path_of(name)
        except ValueError as error:
            raise JobFailed(UPLOAD_GONE) from error
        if not await asyncio.to_thread(path.is_file):
            raise JobFailed(UPLOAD_GONE)
        await context.progress("Leyendo el archivo", 0, 1)
        try:
            _, content = await asyncio.to_thread(read_upload, path, LIMITS, PRISM_LIMITS)
        except MrpackError as error:
            raise JobFailed(str(error)) from error
        await context.progress("Identificando los archivos en Modrinth", 0, 1)
        if isinstance(content, CfContent):
            self._need_curseforge()
            return await self.run_curseforge(content, actor, into, f"{content.name} {content.version_id}".strip())
        return await self.run_content(content, actor, into, _label(content))

    async def run_mrpack(self, data: bytes, actor: Actor, into: Optional[str] = None) -> ImportReport:
        """Import an uploaded .mrpack now (the job's work; tests call it directly)."""
        content = _read(data)
        return await self.run_content(content, actor, into, _label(content))

    async def run_content(self, content: MrpackContent, actor: Actor, into: Optional[str], source: str) -> ImportReport:
        return await self._import(content, actor, into, source=source)

    async def run_curseforge_code(self, code: str, actor: Actor, into: Optional[str]) -> ImportReport:
        assert self.curseforge is not None
        try:
            data = await self.curseforge.shared_profile(code, LIMITS.max_bytes)
        except Exception as error:
            logger.warning("CurseForge profile code %s failed", code, exc_info=True)
            raise HTTP_422_UNPROCESSABLE_ENTITY(CF_CODE_FAILED) from error
        try:
            content = read_cf_zip(data, LIMITS)
        except MrpackError as error:
            # CurseForge answered; what it sent is not a valid pack: say what is wrong with it.
            raise HTTP_422_UNPROCESSABLE_ENTITY(str(error)) from error
        return await self.run_curseforge(content, actor, into, f"{content.name} {content.version_id}".strip())

    async def run_curseforge(self, content: CfContent, actor: Actor, into: Optional[str], source: str) -> ImportReport:
        """A CurseForge pack: its file ids to sha1 in one CurseForge request, those and its override
        jars to Modrinth versions in one more (stored in the catalog), then imported as an .mrpack
        would be. What Modrinth lacks is reported by the manifest's ids (C2, C3)."""
        if into is not None:
            _same_target(await self.modpackDB.bundle_get(into), content)
        assert self.curseforge is not None
        sha1s = await self.curseforge.files_get_many([f.file_id for f in content.files]) if content.files else {}
        wanted = list(dict.fromkeys([*sha1s.values(), *(j.sha1 for j in content.override_jars)]))
        by_sha1 = await self.modrinthAPI.files_hash_post_many(wanted, "sha1") if wanted else {}
        if by_sha1:
            await self.modrinthDB.version_upsert_many(by_sha1.values())
        files: list[IndexFile] = []
        only_cf: list[dict[str, int]] = []
        for file in content.files:
            version = by_sha1.get(sha1s.get(file.file_id, ""))
            sha512 = _sha512_of(version, sha1s.get(file.file_id, "")) if version is not None else None
            if sha512 is None:
                only_cf.append({"project_id": file.project_id, "file_id": file.file_id})
                continue
            files.append(IndexFile(path=f"curseforge/{file.project_id}/{file.file_id}", sha1=sha1s[file.file_id],
                                   sha512=sha512, server=None))
        jars = [j for j in content.override_jars]
        mrpack = MrpackContent(name=content.name, version_id=content.version_id, game_version=content.game_version,
                               loader=content.loader, loader_version=content.loader_version, files=files,
                               override_jars=jars, overrides=dict(content.overrides))
        report = await self._import(mrpack, actor, into, source=source, remote=False)
        report.curseforge_only = only_cf
        return report

    async def run_modrinth(self, ref: str, actor: Actor, into: Optional[str] = None) -> ImportReport:
        """
        Import a modpack published on Modrinth, by its URL (of the project or of one version), slug
        or id, or a version id: without a version, the newest listed one. Its primary file must be on
        Modrinth's CDN and match its sha512. 422 naming why if any of that fails.
        """
        slug, wanted = _parse_ref(ref)
        project, versions = await self._published(slug, wanted)
        if wanted is not None:
            chosen = next((v for v in versions if wanted in (v.version_id, v.version_number)), None)
            if chosen is None:
                raise HTTP_422_UNPROCESSABLE_ENTITY(f"{project.title} no tiene la versión «{wanted}»")
        else:
            listed = [v for v in versions if v.status.value == "listed" and _primary(v) is not None]
            if not listed:
                raise HTTP_422_UNPROCESSABLE_ENTITY(f"{project.title} no tiene ninguna versión publicada")
            chosen = max(listed, key=lambda v: v.date_published)
        data = await self._download(chosen)
        content = _read(data)
        return await self._import(content, actor, into, source=f"{project.title} {chosen.version_number}")

    async def _published(self, slug: str, wanted: Optional[str]) -> tuple[ModrinthProject, list[ModrinthVersion]]:
        """The modpack project and its versions: by slug or id, else by a version id. Two calls."""
        try:
            project = await self.modrinthAPI.project_get(slug)
        except HTTPException as error:
            if error.status_code != 404 or wanted is not None:
                raise
            try:
                version = await self.modrinthAPI.version_get(slug)
            except HTTPException as missing:
                if missing.status_code == 404:
                    raise HTTP_422_UNPROCESSABLE_ENTITY(f"Modrinth no conoce «{slug}»") from missing
                raise
            project = await self.modrinthAPI.project_get(version.project_id)
            self._check_modpack(project)
            return project, [version]
        self._check_modpack(project)
        return project, list(await self.modrinthAPI.project_versions(project.project_id))

    @staticmethod
    def _check_modpack(project: ModrinthProject) -> None:
        if project.project_type.value != "modpack":
            raise HTTP_422_UNPROCESSABLE_ENTITY(f"{project.title} no es un modpack")

    async def _download(self, version: ModrinthVersion) -> bytes:
        primary = _primary(version)
        if primary is None:
            raise HTTP_422_UNPROCESSABLE_ENTITY("Esa versión no tiene un .mrpack")
        if urlparse(primary.url).scheme != "https" or urlparse(primary.url).hostname != DOWNLOAD_HOST:
            raise HTTP_422_UNPROCESSABLE_ENTITY(f"El archivo no está en {DOWNLOAD_HOST}: no se descarga")
        try:
            data = await self.downloader.download_capped(primary.url, LIMITS.max_bytes)
        except TooLarge as error:
            raise HTTP_422_UNPROCESSABLE_ENTITY(f"El archivo pesa más de {LIMITS.max_bytes_label}") from error
        if hashlib.sha512(data).hexdigest() != primary.hashes.sha512.lower():
            raise HTTP_422_UNPROCESSABLE_ENTITY("El archivo descargado no coincide con el que publica Modrinth")
        return data

    async def _import(self, content: MrpackContent, actor: Actor, into: Optional[str], source: str,
                      remote: bool = True) -> ImportReport:
        """Import read content. Without `remote`, its hashes are looked up in the catalog only (the
        caller already asked Modrinth: a CurseForge pack, by sha1)."""
        loader = MinecraftLoaders(content.loader)
        target: Optional[ModpackBundle] = None
        if into is not None:
            target = await self.modpackDB.bundle_get(into)
            _same_target(target, content)
        found = await self._identify([f.sha512 for f in content.files] + [j.sha512 for j in content.override_jars],
                                     remote=remote)
        hinted = await self._hinted(content.hinted_versions)
        project_ids = list(dict.fromkeys([*(found[h].project_id for h in
                                            [f.sha512 for f in content.files] + [j.sha512 for j in content.override_jars]
                                            if h in found and MODRINTH_ID.fullmatch(found[h].project_id)),
                                          *(v.project_id for v in hinted if MODRINTH_ID.fullmatch(v.project_id))]))
        unknown = [f.path for f in content.files if f.sha512 not in found] + \
                  [j.path for j in content.override_jars if j.sha512 not in found]
        if target is None:
            target = await self.modpackService.bundle_create(ModpackBundle(
                name=content.name, description=f"Importado de {source}", version=content.game_version,
                loader=loader, loader_version=content.loader_version), actor)
        bundle_id = str(target.id)
        await self.modpackService.pack_replace(bundle_id, project_ids, actor)
        decisions: dict[str, bool] = {}
        for file in content.files:
            version = found.get(file.sha512)
            if version is not None and file.server in SERVER_ENV and version.project_id in project_ids:
                decisions.setdefault(version.project_id, SERVER_ENV[file.server])
        disabled = [j for j in content.override_jars if j.disabled]
        for jar in disabled:
            version = found.get(jar.sha512)
            if version is not None and version.project_id in project_ids:
                decisions.setdefault(version.project_id, False)
        exceptions: dict[str, str] = {}
        for version in [*found.values(), *hinted]:
            channel = version.version_type.value
            if channel in EXCEPTION_CHANNELS and channel not in target.channels and version.project_id in project_ids:
                exceptions[version.project_id] = channel
        decided = await self.modpackDB.bundle_keys_set_missing(bundle_id, "server_decisions", decisions)
        excepted = await self.modpackDB.bundle_keys_set_missing(bundle_id, "channel_exceptions", exceptions)
        # Each mod pinned to the pack's own version (O2), after its channel exception: the
        # validation counts it. The first file of a project names its version.
        pack_versions: dict[str, ModrinthVersion] = {}
        for version in [*(found[h] for h in [f.sha512 for f in content.files] + [j.sha512 for j in content.override_jars]
                          if h in found), *hinted]:
            if version.project_id in project_ids:
                pack_versions.setdefault(version.project_id, version)
        pinned = await self.modpackService.pins_from_pack(bundle_id, pack_versions, source, actor)
        return ImportReport(bundle_id=bundle_id, created=into is None, source=source, projects=project_ids,
                            unknown=unknown, overrides=dict(content.overrides), decisions=decided,
                            channel_exceptions=excepted, disabled=len(disabled), pinned=pinned.pinned,
                            outside=pinned.outside, offered=pinned.offered, busy=pinned.busy)

    async def _hinted(self, version_ids: list[str]) -> list[ModrinthVersion]:
        """The versions packwiz metafiles name for jars the archive lacks (T6/P4): the catalog first,
        then one batch from Modrinth. One it does not return is left out: a hint, not a requirement."""
        if not version_ids:
            return []
        known = {v.version_id: v for v in await self.modrinthDB.version_find_by_version_ids(version_ids)}
        missing = [v for v in version_ids if v not in known]
        if missing:
            fetched = await self.modrinthAPI.version_get_many(missing)
            if fetched:
                await self.modrinthDB.version_upsert_many(fetched)
            known.update({v.version_id: v for v in fetched})
        return [known[v] for v in version_ids if v in known]

    async def _identify(self, hashes: list[str], remote: bool = True) -> dict[str, ModrinthVersion]:
        """The version each sha512 is a file of: the catalog first, then one Modrinth call for the rest,
        stored in the catalog. A hash neither knows is absent."""
        wanted = list(dict.fromkeys(hashes))
        found = {h: v for v in await self.modrinthDB.version_find_by_sha512(wanted)
                 for h in wanted if any(f.hashes.sha512 == h for f in v.files)}
        missing = [h for h in wanted if h not in found]
        if missing and remote:
            fetched = await self.modrinthAPI.files_hash_post_many(missing, "sha512")
            if fetched:
                await self.modrinthDB.version_upsert_many(fetched.values())
            found.update(fetched)
        return found


def _target(into: Optional[str]) -> AuditTarget:
    return AuditTarget(kind="bundle", id=into or "nuevo")


def _same_target(target: ModpackBundle, content: MrpackContent | CfContent) -> None:
    """409, with nothing changed, if the pack is for another Minecraft version or loader."""
    if (target.version, target.loader) != (content.game_version, MinecraftLoaders(content.loader)):
        raise HTTP_409_CONFLICT(
            f"El archivo es para {content.game_version} con {content.loader}; este pack es "
            f"{target.version} con {target.loader.value}. No se cambió nada.")


def _content(data: dict[str, Any]) -> MrpackContent:
    """A read .mrpack back from a job's payload."""
    return MrpackContent(**{**data, "files": [IndexFile(**f) for f in data["files"]],
                            "override_jars": [OverrideJar(**j) for j in data["override_jars"]]})


def _cf_content(data: dict[str, Any]) -> CfContent:
    """A read CurseForge zip back from a job's payload."""
    return CfContent(**{**data, "files": [CfFile(**f) for f in data["files"]],
                        "override_jars": [OverrideJar(**j) for j in data["override_jars"]]})


def _sha512_of(version: ModrinthVersion, sha1: str) -> Optional[str]:
    """The sha512 of the file of `version` whose sha1 is `sha1`."""
    return next((f.hashes.sha512.lower() for f in version.files if f.hashes.sha1.lower() == sha1), None)


def _read(data: bytes) -> MrpackContent:
    try:
        return read_mrpack(data, LIMITS)
    except MrpackError as error:
        raise HTTP_422_UNPROCESSABLE_ENTITY(str(error)) from error


def _label(content: MrpackContent) -> str:
    return f"{content.name} {content.version_id}".strip()


def _primary(version: ModrinthVersion) -> Optional[ModrinthVersionFile]:
    files = version.files
    return next((f for f in files if f.primary), files[0] if files else None)


def _parse_ref(ref: str) -> tuple[str, Optional[str]]:
    """The slug or id, and the version if the reference names one. A URL must be Modrinth's."""
    ref = ref.strip()
    if "://" in ref or ref.startswith("modrinth.com"):
        parsed = urlparse(ref if "://" in ref else f"https://{ref}")
        found = MODRINTH_URL.fullmatch(parsed.path or "")
        if parsed.hostname not in ("modrinth.com", "www.modrinth.com") or found is None:
            raise HTTP_422_UNPROCESSABLE_ENTITY("Esa dirección no es de Modrinth: usa la de un modpack de modrinth.com")
        return found.group(1), found.group(2)
    if not ref or not re.fullmatch(r"[A-Za-z0-9_.-]+", ref):
        raise HTTP_422_UNPROCESSABLE_ENTITY("Escribe la URL, el slug o el id de un modpack de Modrinth")
    return ref, None
