"""Leer el zip de una instancia de Prism (T6; docs/research/2026-10-09-curseforge-prism-y-deuda-panel.md,
A4–A5): desde el archivo, sin descomprimir los mundos; la raíz es la carpeta de `instance.cfg`; Minecraft
y el loader de `mmc-pack.json`; cada jar de `minecraft/mods/` por su hash, el `.jar.disabled` marcado
(P2); los `.pw.toml` solo como pistas para un jar que falta (P4)."""
import hashlib
import io
import json
import stat
import zipfile
from typing import Any

import pytest

from src.library.mrpack_import import MrpackError
from src.library.prism_import import PrismLimits, is_prism, read_prism

MMC_PACK = {"formatVersion": 1, "components": [
    {"uid": "net.minecraft", "version": "1.21.1"},
    {"uid": "net.fabricmc.intermediary", "version": "1.21.1"},
    {"uid": "net.fabricmc.fabric-loader", "version": "0.16.5"}]}


def instance(files: dict[str, bytes], root: str = "", links: tuple[str, ...] = ()) -> io.BytesIO:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{root}instance.cfg", "[General]\nname=Aventura\n")
        archive.writestr(f"{root}mmc-pack.json", json.dumps(MMC_PACK))
        for name, data in files.items():
            archive.writestr(f"{root}{name}", data)
        for name in links:
            info = zipfile.ZipInfo(f"{root}{name}")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "/etc/passwd")
    buffer.seek(0)
    return buffer


def sha512(data: bytes) -> str:
    return hashlib.sha512(data).hexdigest()


def test_a_flat_instance_gives_its_target_jars_and_disabled_ones() -> None:
    content = read_prism(instance({"minecraft/mods/sodium.jar": b"sodium", "minecraft/mods/iris.jar.disabled": b"iris",
                                   "minecraft/config/sodium.json": b"{}", "minecraft/mods/notes.txt": b"x"}))
    assert (content.name, content.game_version, content.loader, content.loader_version) == ("Aventura", "1.21.1", "fabric", "0.16.5")
    jars = {j.path: (j.sha512, j.disabled) for j in content.override_jars}
    assert jars == {"minecraft/mods/sodium.jar": (sha512(b"sodium"), False),
                    "minecraft/mods/iris.jar.disabled": (sha512(b"iris"), True)}
    assert content.overrides == {"minecraft/config": 1, "minecraft/mods": 1}


def test_the_root_is_where_instance_cfg_is_and_dot_minecraft_counts() -> None:
    content = read_prism(instance({".minecraft/mods/a.jar": b"a"}, root="Aventura/"))
    assert [j.path for j in content.override_jars] == ["Aventura/.minecraft/mods/a.jar"]


def test_worlds_are_counted_never_read() -> None:
    world = b"r" * (3 * 1024 * 1024)
    limits = PrismLimits(max_inflated=1024 * 1024)  # a world read would break it
    content = read_prism(instance({"minecraft/saves/Mundo/region/r.0.0.mca": world, "minecraft/mods/a.jar": b"a"}),
                         limits)
    assert content.overrides == {"minecraft/saves": 1} and len(content.override_jars) == 1


def test_a_pw_toml_without_its_jar_is_a_hint_of_its_modrinth_version() -> None:
    toml = b'name = "Lithium"\nfilename = "lithium.jar"\nside = "both"\n\n[update.modrinth]\nmod-id = "gvQqBUqZ"\nversion = "ZSNsJrPI"\n'
    present = b'name = "Sodium"\nfilename = "sodium.jar"\n\n[update.modrinth]\nmod-id = "AANobbMI"\nversion = "abc"\n'
    content = read_prism(instance({"minecraft/mods/.index/lithium.pw.toml": toml,
                                   "minecraft/mods/.index/sodium.pw.toml": present, "minecraft/mods/sodium.jar": b"s"}))
    assert content.hinted_versions == ["ZSNsJrPI"]


@pytest.mark.parametrize("files,links,reason", [
    ({"minecraft/mods/../../evil.jar": b"x"}, (), "fuera de la instancia"),
    ({}, ("minecraft/mods/link.jar",), "enlace simbólico"),
])
def test_an_unsafe_member_refuses_the_whole_zip(files: dict[str, bytes], links: tuple[str, ...], reason: str) -> None:
    with pytest.raises(MrpackError, match=reason):
        read_prism(instance(files, links=links))


def test_without_mmc_pack_or_a_loader_it_says_why() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("instance.cfg", "name=x\n")
    buffer.seek(0)
    with pytest.raises(MrpackError, match="mmc-pack.json"):
        read_prism(buffer)


def test_is_prism_tells_an_instance_from_an_mrpack() -> None:
    mrpack = io.BytesIO()
    with zipfile.ZipFile(mrpack, "w") as archive:
        archive.writestr("modrinth.index.json", "{}")
    assert is_prism(instance({})) and not is_prism(mrpack)
