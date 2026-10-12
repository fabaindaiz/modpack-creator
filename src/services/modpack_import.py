"""Import a modpack (spec docs/superpowers/specs/2026-10-09-importar-modpacks-design.md).

A .mrpack someone uploads, or one published on Modrinth, becomes a new pack or replaces the content
of one (what the admin added stays). Its files are identified by hash: the catalog first, then one
Modrinth call for every hash it lacks, and the projects in one more. Nothing is pinned: what runs is
recorded as with a server import (d-7985ee-58801d): env.server seeds the server decision and a beta
or alpha outside the pack's channels the channel exception, only where nothing is set.
"""
import asyncio
import logging
import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import IO, Any, Optional
from urllib.parse import urlparse
from fastapi import HTTPException
from src.database import ModpackDatabase, ModrinthDatabase
from src.library.api.client.download import Downloader, TooLarge
from src.library.api.client.modrinth import ModrinthAPI
from src.library.mrpack_import import IndexFile, MrpackContent, MrpackError, MrpackLimits, OverrideJar, read_mrpack
from src.library.prism_import import PrismLimits, read_prism
from src.library.cf_import import CfContent, CfFile, read_cf_zip
from src.library.api.client.curseforge import CurseForgeAPI
from src.database.jobs import JobDatabase
from src.model.jobs import Job
from src.services.jobs import Handler, JobContext, JobFailed
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
            ) -> None:
        self.jobDB = jobDB or JobDatabase()
        # Off without the owner's key (C1): nothing calls CurseForge then.
        self.curseforge = curseforge
        self.modpackService = modpackService
        self.modpackDB = modpackDB
        self.modrinthDB = modrinthDB
        self.modrinthAPI = modrinthAPI
        self.downloader = downloader
        self.audit = audit

    async def import_mrpack(self, data: bytes, actor: Actor, into: Optional[str] = None) -> Job:
        """
        Import an uploaded .mrpack (spec 2026-10-09-economia-de-requests, E7): read now, with no
        request, and identified on Modrinth by the person's import job (one at a time: asking again
        gives the running one). 422 naming why if the file is not a valid .mrpack; 409 if `into` is
        for another Minecraft version or loader. Either way, no job.
        """
        content = _read(data)
        if into is not None:
            _same_target(await self.modpackDB.bundle_get(into), content)
        job, new = await self._job(actor, {"content": asdict(content), "into": into, "source": _label(content)})
        if new:
            await self.audit.record(actor, "bundle.import.request", _target(into),
                                    {"source": _label(content), "job_id": str(job.id)})
        return job

    async def import_prism(self, file: IO[bytes], actor: Actor, into: Optional[str] = None) -> Job:
        """
        Import the zip of a Prism instance (T6): read now from the upload's file (its worlds never
        opened, P1), in a thread since it hashes every jar, and identified on Modrinth by the
        person's import job, as an .mrpack. 422 naming why if it is not an instance; 409 for another
        Minecraft version or loader.
        """
        try:
            content = await asyncio.to_thread(read_prism, file, PRISM_LIMITS)
        except MrpackError as error:
            raise HTTP_422_UNPROCESSABLE_ENTITY(str(error)) from error
        if into is not None:
            _same_target(await self.modpackDB.bundle_get(into), content)
        job, new = await self._job(actor, {"content": asdict(content), "into": into, "source": _label(content)})
        if new:
            await self.audit.record(actor, "bundle.import.request", _target(into),
                                    {"source": _label(content), "job_id": str(job.id)})
        return job

    @property
    def curseforge_enabled(self) -> bool:
        return self.curseforge is not None and self.curseforge.enabled

    async def import_curseforge(self, data: bytes, actor: Actor, into: Optional[str] = None) -> Job:
        """
        Import an uploaded CurseForge modpack zip (T5): read now, with no request; the job turns its
        file ids into sha1 (one CurseForge request) and those into Modrinth versions. 422 without
        the owner's key (C1) or if the zip is not valid; 409 for another Minecraft version or loader.
        """
        self._need_curseforge()
        try:
            content = read_cf_zip(data, LIMITS)
        except MrpackError as error:
            raise HTTP_422_UNPROCESSABLE_ENTITY(str(error)) from error
        if into is not None:
            _same_target(await self.modpackDB.bundle_get(into), content)
        source = f"{content.name} {content.version_id}".strip()
        job, new = await self._job(actor, {"cf": asdict(content), "into": into, "source": source})
        if new:
            await self.audit.record(actor, "bundle.import.request", _target(into), {"source": source, "job_id": str(job.id)})
        return job

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
        saved = job.checkpoint.get("import")
        return job, ImportReport(**saved) if saved else None

    async def _job(self, actor: Actor, payload: dict[str, Any]) -> tuple[Job, bool]:
        """The person's import job: a new one (True), or the one already queued or running (False)."""
        key = f"import:{actor.iss}|{actor.sub}"  # the identity is iss + sub (AGENTS.md)
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
                if "ref" in payload:
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

        await context.step("import", run)

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
        return ImportReport(bundle_id=bundle_id, created=into is None, source=source, projects=project_ids,
                            unknown=unknown, overrides=dict(content.overrides), decisions=decided,
                            channel_exceptions=excepted, disabled=len(disabled))

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
