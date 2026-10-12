"""Read a .mrpack someone uploaded (spec 2026-10-09-importar-modpacks).

Pure: the archive is opened in memory and only read as data, never extracted. It is refused whole,
with its reason, when it breaks a limit or the format: nothing of it is used then. Sizes are counted
while reading, never taken from the zip's own headers (untrusted-package-is-parsed-never-loaded).
"""
import hashlib
import io
import json
import stat
import zipfile
from dataclasses import dataclass, field
from typing import Any, Optional
from src.library.mrpack import DEPENDENCY_KEYS, INDEX, _unsafe_path

LOADERS = {key: loader for loader, key in DEPENDENCY_KEYS.items()}
OVERRIDE_ROOTS = {"overrides": "both", "server-overrides": "server", "client-overrides": "client"}
CHUNK = 64 * 1024


class MrpackError(ValueError):
    """Why an uploaded .mrpack is refused, in words for the panel."""


@dataclass(frozen=True)
class MrpackLimits:
    max_bytes: int = 50 * 1024 * 1024
    max_bytes_label: str = "50 MiB"
    max_entries: int = 5000
    # Everything read from the archive together, inflated.
    max_inflated: int = 200 * 1024 * 1024
    max_index: int = 8 * 1024 * 1024


@dataclass(frozen=True)
class IndexFile:
    path: str
    sha1: str
    sha512: str
    # The file's env.server ("required", "optional", "unsupported"); None when it has no env.
    server: Optional[str]


@dataclass(frozen=True)
class OverrideJar:
    path: str
    sha1: str
    sha512: str
    # Which override folder it came from: "both", "server" or "client".
    side: str
    # A mod Prism turned off (`.jar.disabled`, T6/P2): it comes in «Desactivado».
    disabled: bool = False


@dataclass
class MrpackContent:
    name: str
    version_id: str
    game_version: str
    loader: str
    loader_version: str
    files: list[IndexFile]
    override_jars: list[OverrideJar] = field(default_factory=list)
    # Every other override file, counted by its root and first folder ("overrides/config").
    overrides: dict[str, int] = field(default_factory=dict)
    # Modrinth version ids a packwiz metafile names for a jar the archive does not have (T6/P4).
    hinted_versions: list[str] = field(default_factory=list)


def read_mrpack(data: bytes, limits: MrpackLimits = MrpackLimits()) -> MrpackContent:
    """The pack's target, its indexed files and its overrides; MrpackError naming why if it breaks a
    limit or the .mrpack specification."""
    if len(data) > limits.max_bytes:
        raise MrpackError(f"El archivo pesa más de {limits.max_bytes_label}")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError, EOFError) as error:
        raise MrpackError("El archivo no es un .mrpack: no es un zip") from error
    with archive:
        members = archive.infolist()
        if len(members) > limits.max_entries:
            raise MrpackError(f"El archivo tiene más de {limits.max_entries} entradas")
        for member in members:
            if _unsafe_path(member.filename):
                raise MrpackError(f"«{member.filename}» queda fuera de la instancia")
            if stat.S_ISLNK(member.external_attr >> 16):
                raise MrpackError(f"«{member.filename}» es un enlace simbólico")
        budget = _Budget(limits.max_inflated)
        try:
            content = _content(_index(archive, limits, budget))
            _overrides(archive, members, budget, content)
        except (zipfile.BadZipFile, OSError, EOFError, NotImplementedError, RuntimeError) as error:
            raise MrpackError("El archivo no es un .mrpack: el zip está dañado") from error
    return content


class _Budget:
    """What is left to inflate, for every member read together."""

    def __init__(self, left: int) -> None:
        self.left = left

    def read(self, archive: zipfile.ZipFile, name: str, cap: Optional[int] = None) -> Optional[bytes]:
        """The member's bytes; None past `cap`. MrpackError past the budget."""
        chunks: list[bytes] = []
        size = 0
        with archive.open(name) as stream:
            while chunk := stream.read(CHUNK):
                size += len(chunk)
                self.left -= len(chunk)
                if self.left < 0:
                    raise MrpackError("El archivo descomprimido es demasiado grande")
                if cap is not None and size > cap:
                    return None
                chunks.append(chunk)
        return b"".join(chunks)


def _index(archive: zipfile.ZipFile, limits: MrpackLimits, budget: _Budget) -> dict[str, Any]:
    if INDEX not in archive.namelist():
        raise MrpackError(f"El archivo no es un .mrpack: le falta {INDEX}")
    raw = budget.read(archive, INDEX, cap=limits.max_index)
    if raw is None:
        raise MrpackError(f"{INDEX} es demasiado grande")
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, ValueError) as error:
        raise MrpackError(f"{INDEX} no es JSON válido") from error
    if not isinstance(data, dict):
        raise MrpackError(f"{INDEX} no es un objeto JSON")
    return data


def _content(index: dict[str, Any]) -> MrpackContent:
    if index.get("formatVersion") != 1:
        raise MrpackError("formatVersion debe ser 1")
    if index.get("game") != "minecraft":
        raise MrpackError("game debe ser minecraft")
    dependencies = index.get("dependencies")
    if not isinstance(dependencies, dict) or not isinstance(dependencies.get("minecraft"), str):
        raise MrpackError("Las dependencias no dicen la versión de minecraft")
    loaders = [(LOADERS[key], value) for key, value in dependencies.items() if key in LOADERS]
    if len(loaders) != 1 or not isinstance(loaders[0][1], str):
        raise MrpackError("El pack debe pedir exactamente un loader: fabric, quilt, forge o neoforge")
    files = index.get("files")
    if not isinstance(files, list):
        raise MrpackError("files debe ser una lista")
    return MrpackContent(
        name=_text(index.get("name")) or "Modpack importado", version_id=_text(index.get("versionId")) or "",
        game_version=dependencies["minecraft"], loader=loaders[0][0], loader_version=loaders[0][1],
        files=[_file(f) for f in files])


def _file(file: Any) -> IndexFile:
    if not isinstance(file, dict):
        raise MrpackError("Cada entrada de files debe ser un objeto")
    path = file.get("path")
    if not isinstance(path, str) or _unsafe_path(path):
        raise MrpackError(f"«{path}» queda fuera de la instancia")
    hashes = file.get("hashes")
    sha1 = hashes.get("sha1") if isinstance(hashes, dict) else None
    sha512 = hashes.get("sha512") if isinstance(hashes, dict) else None
    if not isinstance(sha1, str) or not isinstance(sha512, str) or not sha1 or not sha512:
        raise MrpackError(f"{path}: faltan sha1 y sha512")
    env = file.get("env")
    server = env.get("server") if isinstance(env, dict) else None
    return IndexFile(path=path, sha1=sha1.lower(), sha512=sha512.lower(),
                     server=server if server in ("required", "optional", "unsupported") else None)


def _overrides(archive: zipfile.ZipFile, members: list[zipfile.ZipInfo], budget: _Budget, content: MrpackContent) -> None:
    for member in members:
        root, _, rest = member.filename.partition("/")
        if member.is_dir() or root not in OVERRIDE_ROOTS or not rest:
            continue
        first, _, below = rest.partition("/")
        if first == "mods" and below.endswith(".jar") and "/" not in below:
            data = budget.read(archive, member.filename) or b""
            content.override_jars.append(OverrideJar(path=member.filename, sha1=hashlib.sha1(data).hexdigest(),
                                                     sha512=hashlib.sha512(data).hexdigest(), side=OVERRIDE_ROOTS[root]))
        else:
            key = f"{root}/{first}"
            content.overrides[key] = content.overrides.get(key, 0) + 1


def _text(value: Any) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _hash_jar(archive: zipfile.ZipFile, name: str, budget: _Budget) -> tuple[str, str]:
    """sha1 and sha512 of a member, streamed: counted against the budget, never held whole."""
    sha1, sha512 = hashlib.sha1(), hashlib.sha512()
    with archive.open(name) as stream:
        while chunk := stream.read(CHUNK):
            budget.left -= len(chunk)
            if budget.left < 0:
                raise MrpackError("El archivo descomprimido es demasiado grande")
            sha1.update(chunk)
            sha512.update(chunk)
    return sha1.hexdigest(), sha512.hexdigest()
