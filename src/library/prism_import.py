"""Read the zip of a Prism Launcher instance someone uploaded (T6; research
docs/research/2026-10-09-curseforge-prism-y-deuda-panel.md, A4–A5).

Pure, like mrpack_import: read as data from the file, never extracted. Only the member list is read
whole; the jars of `minecraft/mods/`, `mmc-pack.json`, `instance.cfg` and the `.pw.toml` hints are
opened, so a world (`saves/`) costs disk for the upload, never memory or CPU (P1). The root is the
folder that holds `instance.cfg` (old MultiMC exports had a top folder). A `.jar.disabled` is a mod
Prism turned off (P2). A `.pw.toml` is only a hint: its Modrinth version counts when its jar is not
in the zip (P4); identification is by hash.
"""
import hashlib
import json
import stat
import tomllib
import zipfile
from dataclasses import dataclass
from typing import IO, Any, Optional

from src.library.mrpack import _unsafe_path
from src.library.mrpack_import import CHUNK, MrpackContent, MrpackError, OverrideJar, _Budget

# mmc-pack.json component uids → our loader names; anything else (intermediary, LWJGL…) is ignored.
LOADER_UIDS = {"net.fabricmc.fabric-loader": "fabric", "org.quiltmc.quilt-loader": "quilt",
               "net.minecraftforge": "forge", "net.neoforged": "neoforge"}
GAME_DIRS = ("minecraft", ".minecraft")
INDEX_DIR = "mods/.index/"


@dataclass(frozen=True)
class PrismLimits:
    max_bytes: int = 1024 * 1024 * 1024
    max_bytes_label: str = "1 GiB"
    # A world has thousands of region files: they are listed, never read.
    max_entries: int = 200_000
    # What is read (the jars and a few small files), inflated.
    max_inflated: int = 1024 * 1024 * 1024
    max_small: int = 1024 * 1024


def is_prism(file: IO[bytes]) -> bool:
    """Whether the zip is an instance: it has an `instance.cfg` (at the root or in one folder)."""
    try:
        with zipfile.ZipFile(file) as archive:
            return _root(archive.namelist()) is not None
    except (zipfile.BadZipFile, OSError, EOFError):
        return False
    finally:
        file.seek(0)


def read_prism(file: IO[bytes], limits: PrismLimits = PrismLimits()) -> MrpackContent:
    """The instance's target, its mods by hash and the rest counted by folder; MrpackError naming why
    if it breaks a limit or is not an instance."""
    try:
        archive = zipfile.ZipFile(file)
    except (zipfile.BadZipFile, OSError, EOFError) as error:
        raise MrpackError("El archivo no es un zip de Prism: no es un zip") from error
    with archive:
        members = archive.infolist()
        if len(members) > limits.max_entries:
            raise MrpackError(f"El archivo tiene más de {limits.max_entries} entradas")
        for member in members:
            if _unsafe_path(member.filename):
                raise MrpackError(f"«{member.filename}» queda fuera de la instancia")
            if stat.S_ISLNK(member.external_attr >> 16):
                raise MrpackError(f"«{member.filename}» es un enlace simbólico")
        root = _root([m.filename for m in members])
        if root is None:
            raise MrpackError("El archivo no es un zip de Prism: le falta instance.cfg")
        budget = _Budget(limits.max_inflated)
        try:
            return _content(archive, members, root, budget, limits)
        except (zipfile.BadZipFile, OSError, EOFError, NotImplementedError, RuntimeError) as error:
            raise MrpackError("El archivo no es un zip de Prism: el zip está dañado") from error


def _root(names: list[str]) -> Optional[str]:
    """The folder of `instance.cfg`: "" at the root, else "<folder>/" one level down."""
    if "instance.cfg" in names:
        return ""
    nested = sorted(n for n in names if n.count("/") == 1 and n.endswith("/instance.cfg"))
    return nested[0][: -len("instance.cfg")] if nested else None


def _small(archive: zipfile.ZipFile, name: str, budget: _Budget, limits: PrismLimits) -> bytes:
    data = budget.read(archive, name, cap=limits.max_small)
    if data is None:
        raise MrpackError(f"{name} es demasiado grande")
    return data


def _content(archive: zipfile.ZipFile, members: list[zipfile.ZipInfo], root: str, budget: _Budget,
             limits: PrismLimits) -> MrpackContent:
    names = {m.filename for m in members}
    if f"{root}mmc-pack.json" not in names:
        raise MrpackError("El archivo no es un zip de Prism: le falta mmc-pack.json")
    try:
        pack = json.loads(_small(archive, f"{root}mmc-pack.json", budget, limits))
    except (UnicodeDecodeError, ValueError) as error:
        raise MrpackError("mmc-pack.json no es JSON válido") from error
    components = pack.get("components") if isinstance(pack, dict) else None
    if not isinstance(components, list):
        raise MrpackError("mmc-pack.json no tiene componentes")
    versions = {c.get("uid"): c.get("version") for c in components if isinstance(c, dict)}
    game = versions.get("net.minecraft")
    loaders = [(LOADER_UIDS[uid], version) for uid, version in versions.items() if uid in LOADER_UIDS]
    if not isinstance(game, str) or len(loaders) != 1 or not isinstance(loaders[0][1], str):
        raise MrpackError("mmc-pack.json debe decir la versión de Minecraft y un solo loader")
    name = _instance_name(_small(archive, f"{root}instance.cfg", budget, limits)) if f"{root}instance.cfg" in names else None
    content = MrpackContent(name=name or "Instancia de Prism", version_id="", game_version=game, loader=loaders[0][0],
                            loader_version=loaders[0][1], files=[])
    hints: list[dict[str, Any]] = []
    jars: set[str] = set()
    for member in members:
        if member.is_dir() or not member.filename.startswith(root):
            continue
        relative = member.filename[len(root):]
        game_dir, _, rest = relative.partition("/")
        if game_dir not in GAME_DIRS or not rest:
            continue
        first, _, below = rest.partition("/")
        if first == "mods" and "/" not in below and (below.endswith(".jar") or below.endswith(".jar.disabled")):
            sha1, sha512 = _hash(archive, member.filename, budget)
            content.override_jars.append(OverrideJar(path=member.filename, sha1=sha1, sha512=sha512, side="both",
                                                     disabled=below.endswith(".disabled")))
            jars.add(below.removesuffix(".disabled"))
        elif rest.startswith(INDEX_DIR) and rest.endswith(".pw.toml") and rest.count("/") == 2:
            hints.append(_hint(_small(archive, member.filename, budget, limits)))
        else:
            key = f"{game_dir}/{first}"
            content.overrides[key] = content.overrides.get(key, 0) + 1
    content.hinted_versions = [h["version"] for h in hints if h.get("version") and h.get("filename") not in jars]
    return content


def _hash(archive: zipfile.ZipFile, name: str, budget: _Budget) -> tuple[str, str]:
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


def _instance_name(raw: bytes) -> Optional[str]:
    for line in raw.decode("utf-8", errors="replace").splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "name" and value.strip():
            return value.strip()[:120]
    return None


def _hint(raw: bytes) -> dict[str, Any]:
    """A packwiz metafile's file name and Modrinth version; empty if it is not readable."""
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}
    modrinth = data.get("update", {}).get("modrinth", {}) if isinstance(data.get("update"), dict) else {}
    version = modrinth.get("version") if isinstance(modrinth, dict) else None
    filename = data.get("filename")
    return {"filename": filename if isinstance(filename, str) else None,
            "version": version if isinstance(version, str) and version.isalnum() and len(version) <= 16 else None}
