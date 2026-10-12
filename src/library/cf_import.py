"""Read the zip of a CurseForge modpack someone uploaded (T5; research
docs/research/2026-10-09-curseforge-prism-y-deuda-panel.md, A2 and A5).

Pure, like mrpack_import: read as data, never extracted, refused whole with its reason. The
manifest names each mod by CurseForge project and file id; nothing here asks CurseForge: the import
job turns the file ids into sha1 hashes (one CurseForge request) and those into Modrinth versions.
`manifest.json` has no published schema: what is read is what Prism and packwiz read.
"""
import io
import json
import stat
import zipfile
from dataclasses import dataclass, field
from typing import IO, Any

from src.library.mrpack import _unsafe_path
from src.library.mrpack_import import MrpackError, MrpackLimits, OverrideJar, _Budget, _hash_jar

MANIFEST = "manifest.json"
LOADER_PREFIXES = ("neoforge", "forge", "fabric", "quilt")


@dataclass(frozen=True)
class CfFile:
    project_id: int
    file_id: int
    # `required: false` in the manifest: it comes in «Por decidir» (C4).
    required: bool


@dataclass
class CfContent:
    name: str
    version_id: str
    game_version: str
    loader: str
    loader_version: str
    files: list[CfFile]
    override_jars: list[OverrideJar] = field(default_factory=list)
    overrides: dict[str, int] = field(default_factory=dict)


def is_curseforge(file: IO[bytes]) -> bool:
    """Whether the zip has a CurseForge `manifest.json` at its root."""
    try:
        with zipfile.ZipFile(file) as archive:
            return MANIFEST in archive.namelist()
    except (zipfile.BadZipFile, OSError, EOFError):
        return False
    finally:
        file.seek(0)


def read_cf_zip(data: bytes, limits: MrpackLimits = MrpackLimits()) -> CfContent:
    """The pack's target, its files by CurseForge id and its overrides; MrpackError naming why."""
    if len(data) > limits.max_bytes:
        raise MrpackError(f"El archivo pesa más de {limits.max_bytes_label}")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError, EOFError) as error:
        raise MrpackError("El archivo no es un zip de CurseForge: no es un zip") from error
    with archive:
        members = archive.infolist()
        if len(members) > limits.max_entries:
            raise MrpackError(f"El archivo tiene más de {limits.max_entries} entradas")
        for member in members:
            if _unsafe_path(member.filename):
                raise MrpackError(f"«{member.filename}» queda fuera del pack")
            if stat.S_ISLNK(member.external_attr >> 16):
                raise MrpackError(f"«{member.filename}» es un enlace simbólico")
        budget = _Budget(limits.max_inflated)
        try:
            if MANIFEST not in archive.namelist():
                raise MrpackError(f"El archivo no es un zip de CurseForge: le falta {MANIFEST}")
            raw = budget.read(archive, MANIFEST, cap=limits.max_index)
            if raw is None:
                raise MrpackError(f"{MANIFEST} es demasiado grande")
            content, root = _content(raw)
            _overrides(archive, members, root, budget, content)
        except (zipfile.BadZipFile, OSError, EOFError, NotImplementedError, RuntimeError) as error:
            raise MrpackError("El archivo no es un zip de CurseForge: el zip está dañado") from error
    return content


def _content(raw: bytes) -> tuple[CfContent, str]:
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, ValueError) as error:
        raise MrpackError(f"{MANIFEST} no es JSON válido") from error
    if not isinstance(data, dict) or data.get("manifestType") != "minecraftModpack":
        raise MrpackError("manifestType debe ser minecraftModpack")
    if data.get("manifestVersion") != 1:
        raise MrpackError("manifestVersion debe ser 1")
    minecraft = data.get("minecraft")
    game = minecraft.get("version") if isinstance(minecraft, dict) else None
    loaders = minecraft.get("modLoaders") if isinstance(minecraft, dict) else None
    primary = [l for l in loaders if isinstance(l, dict) and l.get("primary")] if isinstance(loaders, list) else []
    if not isinstance(game, str) or len(primary) != 1 or not isinstance(primary[0].get("id"), str):
        raise MrpackError(f"{MANIFEST} debe decir la versión de Minecraft y un loader primario")
    loader, loader_version = _loader(primary[0]["id"], game)
    files = data.get("files")
    if not isinstance(files, list) or not all(
            isinstance(f, dict) and type(f.get("projectID")) is int and type(f.get("fileID")) is int for f in files):
        raise MrpackError("files debe ser una lista de projectID y fileID enteros")
    root = data.get("overrides", "overrides")
    if not isinstance(root, str) or not root or _unsafe_path(root) or "/" in root.strip("/"):
        raise MrpackError(f"«{root}» queda fuera del pack")
    raw_name, raw_version = data.get("name"), data.get("version")
    name = raw_name if isinstance(raw_name, str) and raw_name.strip() else "Modpack de CurseForge"
    version = raw_version if isinstance(raw_version, str) else ""
    return CfContent(name=name.strip()[:120], version_id=version.strip()[:60], game_version=game, loader=loader,
                     loader_version=loader_version,
                     files=[CfFile(project_id=f["projectID"], file_id=f["fileID"], required=f.get("required") is not False)
                            for f in files]), root.strip("/")


def _loader(loader_id: str, game: str) -> tuple[str, str]:
    """`forge-47.2.0` → forge, 47.2.0; NeoForge for 1.20.1 repeats the game version (`neoforge-1.20.1-47.1.106`)."""
    for prefix in LOADER_PREFIXES:
        if loader_id.startswith(prefix + "-"):
            version = loader_id[len(prefix) + 1:]
            if version.startswith(game + "-"):
                version = version[len(game) + 1:]
            if version:
                return prefix, version
    raise MrpackError(f"El loader «{loader_id}» no es fabric, quilt, forge ni neoforge")


def _overrides(archive: zipfile.ZipFile, members: list[zipfile.ZipInfo], root: str, budget: _Budget,
               content: CfContent) -> None:
    for member in members:
        top, _, rest = member.filename.partition("/")
        if member.is_dir() or top != root or not rest:
            continue
        first, _, below = rest.partition("/")
        if first == "mods" and below.endswith(".jar") and "/" not in below:
            sha1, sha512 = _hash_jar(archive, member.filename, budget)
            content.override_jars.append(OverrideJar(path=member.filename, sha1=sha1, sha512=sha512, side="both"))
        else:
            key = f"{root}/{first}"
            content.overrides[key] = content.overrides.get(key, 0) + 1
