"""A build as a .mrpack, the pack players import (i-7985ee-7352b3).

Pure functions, following Modrinth's format specification
(https://support.modrinth.com/en/articles/8802351-modrinth-modpack-format-mrpack). Every index is
validated against it before it is written (validate-each-transformation-run): an export that
breaks the spec is refused, never served. Files are only referenced by their Modrinth download
URL, never embedded.
"""
import io
import json
import re
import zipfile
from collections.abc import Iterable, Mapping
from typing import Any, Optional
from urllib.parse import urlparse
from src.library.resolution import LockEntry

INDEX = "modrinth.index.json"
ALLOWED_DOMAINS = {"cdn.modrinth.com", "github.com", "raw.githubusercontent.com", "gitlab.com"}
DEPENDENCY_KEYS = {"fabric": "fabric-loader", "quilt": "quilt-loader", "neoforge": "neoforge", "forge": "forge"}
KNOWN_DEPENDENCIES = {"minecraft", *DEPENDENCY_KEYS.values()}
FOLDERS = {"mod": "mods", "shader": "shaderpacks", "resourcepack": "resourcepacks", "datapack": "datapacks"}
ENV_VALUES = {"required", "optional", "unsupported"}

# The client side from Modrinth's environment. The server side is the build's own decision
# (d-7985ee-f0c7a6), so it is not read from here.
CLIENT_ENV = {
    "client_and_server": "required", "client_only": "required", "client_only_server_optional": "required",
    "singleplayer_only": "required", "client_or_server_prefers_both": "required",
    "server_only_client_optional": "optional", "client_or_server": "optional",
    "server_only": "unsupported", "dedicated_server_only": "unsupported",
}


def client_env(environment: Optional[str], project_type: str) -> str:
    """
    Whether a player's instance needs the file.

    Shaders and resource packs are the client's; a datapack lives in a world on the server. A mod
    follows its environment; a missing or unknown one installs on the client, as a launcher does
    with a file that has no `env` (absence is not "unsupported").
    """
    if project_type in ("shader", "resourcepack"):
        return "required"
    if project_type == "datapack":
        return "unsupported"
    return CLIENT_ENV.get(environment or "", "required")


def mrpack_index(name: str, version_id: str, summary: str, game_version: str, loader: str,
                 loader_version: str, entries: Iterable[LockEntry]) -> dict[str, Any]:
    """The modrinth.index.json of a build: every lock entry, by its Modrinth URL and hashes."""
    files = []
    for entry in entries:
        if entry.file is None:
            raise ValueError(f"{entry.project_id} has no file")
        files.append({
            "path": f"{FOLDERS.get(entry.project_type, 'mods')}/{entry.file.filename}",
            "hashes": {"sha1": entry.file.sha1, "sha512": entry.file.sha512},
            "env": {"client": client_env(entry.environment, entry.project_type),
                    "server": "required" if entry.server == "include" else "unsupported"},
            "downloads": [entry.file.url],
            "fileSize": entry.file.size,
        })
    return {
        "formatVersion": 1, "game": "minecraft", "versionId": version_id, "name": name, "summary": summary,
        "files": files,
        "dependencies": {"minecraft": game_version, DEPENDENCY_KEYS.get(loader, loader): loader_version},
    }


def _unsafe_path(path: str) -> bool:
    parts = re.split(r"[\\/]", path)
    return (not path or path.startswith(("/", "\\")) or bool(re.match(r"^[A-Za-z]:", path))
            or ".." in parts)


def validate_index(index: Mapping[str, Any]) -> list[str]:
    """Everything in an index that breaks the .mrpack specification; empty if nothing does."""
    problems = []
    if index.get("formatVersion") != 1:
        problems.append("formatVersion must be 1")
    if index.get("game") != "minecraft":
        problems.append("game must be minecraft")
    for key in ("versionId", "name"):
        if not isinstance(index.get(key), str) or not index.get(key):
            problems.append(f"{key} is required")
    dependencies = index.get("dependencies")
    if not isinstance(dependencies, Mapping) or "minecraft" not in dependencies:
        problems.append("dependencies must name the minecraft version")
    else:
        problems += [f"unknown dependency {key}" for key in dependencies if key not in KNOWN_DEPENDENCIES]
    for file in index.get("files", []):
        path = file.get("path", "")
        if _unsafe_path(path):
            problems.append(f"{path}: the path must stay inside the instance")
        hashes = file.get("hashes", {})
        if not hashes.get("sha1") or not hashes.get("sha512"):
            problems.append(f"{path}: sha1 and sha512 are required")
        if not isinstance(file.get("fileSize"), int):
            problems.append(f"{path}: fileSize is required")
        env = file.get("env")
        if env is not None and not ({env.get("client"), env.get("server")} <= ENV_VALUES):
            problems.append(f"{path}: env values must be required, optional or unsupported")
        downloads = file.get("downloads") or []
        if not downloads:
            problems.append(f"{path}: at least one download is required")
        for url in downloads:
            parsed = urlparse(url)
            if parsed.scheme != "https" or " " in url:
                problems.append(f"{path}: {url} must be an HTTPS URL without spaces")
            elif parsed.hostname not in ALLOWED_DOMAINS:
                problems.append(f"{path}: {parsed.hostname} is not an allowed download domain")
    return problems


def write_mrpack(index: Mapping[str, Any]) -> bytes:
    """The .mrpack archive of an index; ValueError, naming every problem, if it breaks the spec."""
    problems = validate_index(index)
    if problems:
        raise ValueError("; ".join(problems))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(INDEX, json.dumps(index, indent=2, ensure_ascii=False))
    return buffer.getvalue()


# The loader version a build freezes, from each loader's own metadata (research §2).

def fabric_loader_version(answer: Iterable[Mapping[str, Any]]) -> Optional[str]:
    """meta.fabricmc.net /v2/versions/loader/{mc}: newest first; the first stable one."""
    return next((item["loader"]["version"] for item in answer if item["loader"].get("stable")), None)


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version))


def quilt_loader_version(answer: Iterable[Mapping[str, Any]]) -> Optional[str]:
    """meta.quiltmc.org /v3/versions/loader/{mc}: neither sorted nor filtered; the highest
    version that is not a beta."""
    releases = [item["loader"]["version"] for item in answer if "-" not in item["loader"]["version"]]
    return max(releases, key=_version_key, default=None)
