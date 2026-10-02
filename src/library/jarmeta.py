"""A mod's id and name from its jar, read as data (d-7985ee-c8a15d).

A jar from a server is untrusted (untrusted-package-is-parsed-never-loaded): it is opened as a
zip, only the known metadata files are read, each with a cap on the bytes inflated whatever size
the zip declares, and nothing is extracted or executed. Anything unreadable gives None.
"""
import io
import json
import tomllib
import zipfile
from typing import Any, Optional
from pydantic import BaseModel

MAX_METADATA_BYTES = 256 * 1024
# The metadata files, by exact name at the root of the jar, and the loader each one means.
METADATA_FILES = {
    "fabric.mod.json": "fabric",
    "quilt.mod.json": "quilt",
    "META-INF/neoforge.mods.toml": "neoforge",
    "META-INF/mods.toml": "forge",
}

class ModMetadata(BaseModel):
    loader: str
    mod_id: str
    name: Optional[str] = None
    version: Optional[str] = None

def _read_capped(archive: zipfile.ZipFile, name: str) -> Optional[bytes]:
    """The member's bytes, or None past MAX_METADATA_BYTES: read up to the cap from the stream,
    never trusting the declared size."""
    with archive.open(name) as member:
        data = member.read(MAX_METADATA_BYTES + 1)
    return data if len(data) <= MAX_METADATA_BYTES else None

def _text(value: Any) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None

def _version(value: Any) -> Optional[str]:
    text = _text(value)
    return None if text is None or "${" in text else text

def _parse(loader: str, data: bytes) -> Optional[ModMetadata]:
    if loader == "fabric":
        doc = json.loads(data.decode("utf-8"), strict=False)
        if not isinstance(doc, dict):
            return None
        mod_id, name, version = doc.get("id"), doc.get("name"), doc.get("version")
    elif loader == "quilt":
        doc = json.loads(data.decode("utf-8"), strict=False)
        quilt = doc.get("quilt_loader") if isinstance(doc, dict) else None
        if not isinstance(quilt, dict):
            return None
        metadata: Any = quilt.get("metadata")
        name = metadata.get("name") if isinstance(metadata, dict) else None
        mod_id, version = quilt.get("id"), quilt.get("version")
    else:
        mods = tomllib.loads(data.decode("utf-8")).get("mods")
        first = mods[0] if isinstance(mods, list) and mods and isinstance(mods[0], dict) else None
        if first is None:
            return None
        mod_id, name, version = first.get("modId"), first.get("displayName"), first.get("version")
    identifier = _text(mod_id)
    if identifier is None:
        return None
    return ModMetadata(loader=loader, mod_id=identifier, name=_text(name), version=_version(version))

def read_mod_metadata(jar: bytes) -> Optional[ModMetadata]:
    """The first known metadata file of a jar, parsed; None if there is none or it cannot be read."""
    try:
        with zipfile.ZipFile(io.BytesIO(jar)) as archive:
            names = set(archive.namelist())
            for name, loader in METADATA_FILES.items():
                if name not in names:
                    continue
                data = _read_capped(archive, name)
                if data is None:
                    return None
                return _parse(loader, data)
    except (zipfile.BadZipFile, UnicodeDecodeError, ValueError, tomllib.TOMLDecodeError, KeyError, OSError, EOFError):
        return None
    return None
