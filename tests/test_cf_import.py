"""Leer el zip de un modpack de CurseForge (T5; docs/research/2026-10-09-curseforge-prism-y-deuda-panel.md,
A2 y A5): `manifest.json` con `manifestType` "minecraftModpack" y versión 1; Minecraft y el loader
primario de `modLoaders`; `files` como (proyecto, archivo, requerido); los jars de la carpeta de
overrides por su hash, y el resto contado. Puro, como el .mrpack: nada se extrae."""
import hashlib
import io
import json
import zipfile
from typing import Any

import pytest

from src.library.cf_import import is_curseforge, read_cf_zip
from src.library.mrpack_import import MrpackError


def manifest(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "minecraft": {"version": "1.20.1", "modLoaders": [{"id": "forge-47.2.0", "primary": True}]},
        "manifestType": "minecraftModpack", "manifestVersion": 1, "name": "Aventura", "version": "2.0",
        "author": "x", "files": [{"projectID": 238222, "fileID": 4567890, "required": True},
                                 {"projectID": 306612, "fileID": 5000001, "required": False}],
        "overrides": "overrides"}
    data.update(changes)
    return data


def cf_zip(data: dict[str, Any], members: dict[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", json.dumps(data))
        for name, content in (members or {}).items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_the_manifest_gives_target_files_and_overrides() -> None:
    content = read_cf_zip(cf_zip(manifest(), {"overrides/mods/own.jar": b"own", "overrides/config/a.toml": b""}))
    assert (content.name, content.version_id, content.game_version, content.loader, content.loader_version) == \
        ("Aventura", "2.0", "1.20.1", "forge", "47.2.0")
    assert [(f.project_id, f.file_id, f.required) for f in content.files] == [(238222, 4567890, True), (306612, 5000001, False)]
    assert [(j.path, j.sha1) for j in content.override_jars] == [("overrides/mods/own.jar", hashlib.sha1(b"own").hexdigest())]
    assert content.overrides == {"overrides/config": 1}


@pytest.mark.parametrize("loader_id,expected", [
    ("neoforge-21.1.77", ("neoforge", "21.1.77")),
    ("neoforge-1.20.1-47.1.106", ("neoforge", "47.1.106")),  # NeoForge for 1.20.1 repeats the game version
    ("fabric-0.16.5", ("fabric", "0.16.5")),
    ("quilt-0.26.0", ("quilt", "0.26.0")),
])
def test_the_primary_loader_is_read_from_its_id(loader_id: str, expected: tuple[str, str]) -> None:
    data = manifest(minecraft={"version": "1.20.1", "modLoaders": [{"id": "forge-1", "primary": False},
                                                                    {"id": loader_id, "primary": True}]})
    content = read_cf_zip(cf_zip(data))
    assert (content.loader, content.loader_version) == expected


@pytest.mark.parametrize("data,reason", [
    (manifest(manifestType="minecraftResourcePack"), "minecraftModpack"),
    (manifest(manifestVersion=2), "manifestVersion"),
    (manifest(minecraft={"version": "1.20.1", "modLoaders": []}), "loader"),
    (manifest(files=[{"projectID": "x", "fileID": 1}]), "files"),
    (manifest(overrides="../fuera"), "fuera de"),
])
def test_a_bad_manifest_says_why(data: dict[str, Any], reason: str) -> None:
    with pytest.raises(MrpackError, match=reason):
        read_cf_zip(cf_zip(data))


def test_is_curseforge_tells_a_cf_zip_from_others() -> None:
    other = io.BytesIO()
    with zipfile.ZipFile(other, "w") as archive:
        archive.writestr("modrinth.index.json", "{}")
    assert is_curseforge(io.BytesIO(cf_zip(manifest()))) and not is_curseforge(other)
