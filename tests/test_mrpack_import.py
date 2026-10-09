"""Leer un .mrpack subido (spec 2026-10-09-importar-modpacks): se abre en memoria y solo se lee
como dato. Cada regla de rechazo tiene su archivo plantado; un rechazo dice por qué y no deja nada."""
import hashlib
import io
import json
import stat
import zipfile
from typing import Any, Optional

import pytest

from src.library.mrpack_import import MrpackError, MrpackLimits, read_mrpack

SHA1, SHA512 = "a" * 40, "b" * 128


def index(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "formatVersion": 1, "game": "minecraft", "versionId": "1.4.0", "name": "Mi Pack",
        "dependencies": {"minecraft": "1.20.1", "fabric-loader": "0.16.10"},
        "files": [
            {"path": "mods/sodium.jar", "hashes": {"sha1": SHA1, "sha512": SHA512}, "fileSize": 10,
             "downloads": ["https://cdn.modrinth.com/data/x/sodium.jar"], "env": {"client": "required", "server": "unsupported"}},
            {"path": "mods/lithium.jar", "hashes": {"sha1": "c" * 40, "sha512": "d" * 128}, "fileSize": 10,
             "downloads": ["https://example.org/lithium.jar"]},
        ],
    }
    data.update(changes)
    return data


def mrpack(data: Optional[dict[str, Any]] = None, members: Optional[dict[str, bytes]] = None,
           raw_index: Optional[bytes] = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("modrinth.index.json", raw_index if raw_index is not None else json.dumps(data or index()))
        for name, content in (members or {}).items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_reads_the_pack_target_and_each_file_with_its_hashes_and_server_side() -> None:
    content = read_mrpack(mrpack())
    assert (content.name, content.version_id, content.game_version) == ("Mi Pack", "1.4.0", "1.20.1")
    assert (content.loader, content.loader_version) == ("fabric", "0.16.10")
    assert [(f.path, f.sha1, f.sha512, f.server) for f in content.files] == [
        ("mods/sodium.jar", SHA1, SHA512, "unsupported"), ("mods/lithium.jar", "c" * 40, "d" * 128, None)]


@pytest.mark.parametrize("key, loader", [("fabric-loader", "fabric"), ("quilt-loader", "quilt"),
                                         ("forge", "forge"), ("neoforge", "neoforge")])
def test_the_loader_comes_from_the_dependencies(key: str, loader: str) -> None:
    content = read_mrpack(mrpack(index(dependencies={"minecraft": "1.21.1", key: "1.0"})))
    assert (content.loader, content.loader_version, content.game_version) == (loader, "1.0", "1.21.1")


def test_overrides_are_counted_by_folder_and_their_jars_hashed() -> None:
    jar = b"a jar inside the overrides"
    content = read_mrpack(mrpack(members={
        "overrides/config/sodium.json": b"{}", "overrides/config/lithium.toml": b"",
        "overrides/kubejs/startup.js": b"//", "server-overrides/config/server.toml": b"",
        "client-overrides/options.txt": b"", "overrides/mods/private.jar": jar, "overrides/": b""}))
    assert content.overrides == {"overrides/config": 2, "overrides/kubejs": 1, "server-overrides/config": 1,
                                 "client-overrides/options.txt": 1}
    assert [(j.path, j.sha1, j.sha512, j.side) for j in content.override_jars] == [
        ("overrides/mods/private.jar", hashlib.sha1(jar).hexdigest(), hashlib.sha512(jar).hexdigest(), "both")]


def refused(data: bytes, limits: MrpackLimits = MrpackLimits()) -> str:
    with pytest.raises(MrpackError) as error:
        read_mrpack(data, limits)
    return str(error.value)


def test_not_a_zip_is_refused() -> None:
    assert "no es un .mrpack" in refused(b"not a zip at all")


def test_a_zip_without_an_index_is_refused() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", "{}")  # a CurseForge zip, say
    assert "modrinth.index.json" in refused(buffer.getvalue())


def test_an_index_that_is_not_json_is_refused() -> None:
    assert "modrinth.index.json" in refused(mrpack(raw_index=b"{not json"))


@pytest.mark.parametrize("changes, reason", [
    ({"formatVersion": 2}, "formatVersion"),
    ({"game": "terraria"}, "game"),
    ({"dependencies": {"fabric-loader": "0.16"}}, "minecraft"),
    ({"dependencies": {"minecraft": "1.20.1"}}, "loader"),
    ({"dependencies": {"minecraft": "1.20.1", "fabric-loader": "1", "forge": "2"}}, "loader"),
    ({"files": [{"path": "../../evil.jar", "hashes": {"sha1": SHA1, "sha512": SHA512}, "fileSize": 1,
                 "downloads": ["https://cdn.modrinth.com/x"]}]}, "evil.jar"),
    ({"files": [{"path": "mods/a.jar", "hashes": {"sha1": SHA1}, "fileSize": 1,
                 "downloads": ["https://cdn.modrinth.com/x"]}]}, "sha512"),
    ({"files": "everything"}, "files"),
])
def test_an_index_against_the_specification_is_refused_with_its_reason(changes: dict[str, Any], reason: str) -> None:
    assert reason in refused(mrpack(index(**changes)))


@pytest.mark.parametrize("name", ["overrides/../../etc/passwd", "/etc/passwd", "C:/evil.jar", "overrides\\..\\x"])
def test_a_member_outside_the_instance_is_refused(name: str) -> None:
    assert "fuera" in refused(mrpack(members={name: b"x"}))


def test_a_symlink_member_is_refused() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("modrinth.index.json", json.dumps(index()))
        link = zipfile.ZipInfo("overrides/config/link")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "/etc/passwd")
    assert "enlace" in refused(buffer.getvalue())


def test_too_large_is_refused_before_opening_it() -> None:
    assert "50" in refused(b"x" * 101, MrpackLimits(max_bytes=100, max_bytes_label="50 MiB"))


def test_too_many_entries_is_refused() -> None:
    data = mrpack(members={f"overrides/config/{i}.txt": b"" for i in range(5)})
    assert "entradas" in refused(data, MrpackLimits(max_entries=5))


def test_a_bomb_is_refused_by_what_it_inflates_to_not_what_it_says() -> None:
    # Only the index and the override jars are inflated (the rest is only counted): a bomb in a jar.
    data = mrpack(members={"overrides/mods/big.jar": b"\0" * 10_000})
    assert "descomprimido" in refused(data, MrpackLimits(max_inflated=5_000))


def test_a_bomb_among_the_other_overrides_is_never_inflated() -> None:
    data = mrpack(members={"overrides/config/big.txt": b"\0" * 10_000})
    assert read_mrpack(data, MrpackLimits(max_inflated=5_000)).overrides == {"overrides/config": 1}


def test_an_index_too_large_is_refused() -> None:
    assert "modrinth.index.json" in refused(mrpack(), MrpackLimits(max_index=10))
