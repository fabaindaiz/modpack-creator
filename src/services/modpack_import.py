"""Import a modpack (spec docs/superpowers/specs/2026-10-09-importar-modpacks-design.md).

A .mrpack someone uploads, or one published on Modrinth, becomes a new pack or replaces the content
of one (what the admin added stays). Its files are identified by hash: the catalog first, then one
Modrinth call for every hash it lacks, and the projects in one more. Nothing is pinned: what runs is
recorded as with a server import (d-7985ee-58801d): env.server seeds the server decision and a beta
or alpha outside the pack's channels the channel exception, only where nothing is set.
"""
import hashlib
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse
from fastapi import HTTPException
from src.database import ModpackDatabase, ModrinthDatabase
from src.library.api.client.download import Downloader, TooLarge
from src.library.api.client.modrinth import ModrinthAPI
from src.library.mrpack_import import MrpackContent, MrpackError, MrpackLimits, read_mrpack
from src.library.resolution import EXCEPTION_CHANNELS
from src.model.audit import AuditTarget
from src.model.modpack import ModpackBundle
from src.model.modrinth import ModrinthProject, ModrinthVersion
from src.model.modrinth.version import ModrinthVersionFile
from src.model.users import Actor
from src.model.utils import MinecraftLoaders
from src.services.audit import AuditSink
from src.services.modpack import MODRINTH_ID, ModpackService
from src.exceptions import HTTP_409_CONFLICT, HTTP_422_UNPROCESSABLE_ENTITY

LIMITS = MrpackLimits()
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


class ModpackImportService():
    def __init__(self,
            modpackService: ModpackService,
            modpackDB: ModpackDatabase,
            modrinthDB: ModrinthDatabase,
            modrinthAPI: ModrinthAPI,
            downloader: Downloader,
            audit: AuditSink,
            ) -> None:
        self.modpackService = modpackService
        self.modpackDB = modpackDB
        self.modrinthDB = modrinthDB
        self.modrinthAPI = modrinthAPI
        self.downloader = downloader
        self.audit = audit

    async def import_mrpack(self, data: bytes, actor: Actor, into: Optional[str] = None) -> ImportReport:
        """
        Import an uploaded .mrpack: a new pack, or the content of `into`. 422 naming why if the file
        is not a valid .mrpack; 409 if `into` is for another Minecraft version or loader. Either way,
        nothing is written.
        """
        content = _read(data)
        report = await self._import(content, actor, into, source=_label(content))
        await self.audit.record(actor, "bundle.import", _target(report), _details(report))
        return report

    async def import_modrinth(self, ref: str, actor: Actor, into: Optional[str] = None) -> ImportReport:
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
        report = await self._import(content, actor, into, source=f"{project.title} {chosen.version_number}")
        await self.audit.record(actor, "bundle.import", _target(report), _details(report))
        return report

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

    async def _import(self, content: MrpackContent, actor: Actor, into: Optional[str], source: str) -> ImportReport:
        loader = MinecraftLoaders(content.loader)
        target: Optional[ModpackBundle] = None
        if into is not None:
            target = await self.modpackDB.bundle_get(into)
            if (target.version, target.loader) != (content.game_version, loader):
                raise HTTP_409_CONFLICT(
                    f"El archivo es para {content.game_version} con {content.loader}; este pack es "
                    f"{target.version} con {target.loader.value}. No se cambió nada.")
        found = await self._identify([f.sha512 for f in content.files] + [j.sha512 for j in content.override_jars])
        project_ids = list(dict.fromkeys(found[h].project_id for h in
                                         [f.sha512 for f in content.files] + [j.sha512 for j in content.override_jars]
                                         if h in found and MODRINTH_ID.fullmatch(found[h].project_id)))
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
        exceptions: dict[str, str] = {}
        for version in found.values():
            channel = version.version_type.value
            if channel in EXCEPTION_CHANNELS and channel not in target.channels and version.project_id in project_ids:
                exceptions[version.project_id] = channel
        decided = await self.modpackDB.bundle_keys_set_missing(bundle_id, "server_decisions", decisions)
        excepted = await self.modpackDB.bundle_keys_set_missing(bundle_id, "channel_exceptions", exceptions)
        return ImportReport(bundle_id=bundle_id, created=into is None, source=source, projects=project_ids,
                            unknown=unknown, overrides=dict(content.overrides), decisions=decided,
                            channel_exceptions=excepted)

    async def _identify(self, hashes: list[str]) -> dict[str, ModrinthVersion]:
        """The version each sha512 is a file of: the catalog first, then one Modrinth call for the rest,
        stored in the catalog. A hash neither knows is absent."""
        wanted = list(dict.fromkeys(hashes))
        found = {h: v for v in await self.modrinthDB.version_find_by_sha512(wanted)
                 for h in wanted if any(f.hashes.sha512 == h for f in v.files)}
        missing = [h for h in wanted if h not in found]
        if missing:
            fetched = await self.modrinthAPI.files_hash_post_many(missing, "sha512")
            if fetched:
                await self.modrinthDB.version_upsert_many(fetched.values())
            found.update(fetched)
        return found


def _target(report: ImportReport) -> AuditTarget:
    return AuditTarget(kind="bundle", id=report.bundle_id)


def _details(report: ImportReport) -> dict[str, object]:
    return {"source": report.source, "created": report.created, "projects": len(report.projects),
            "unknown": report.unknown, "overrides": report.overrides, "decisions": report.decisions,
            "channel_exceptions": report.channel_exceptions}


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
