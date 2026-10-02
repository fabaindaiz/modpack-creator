"""La metadata de un jar, leída como dato (d-7985ee-c8a15d; tarjeta untrusted-package-is-parsed-never-loaded).

Un jar del servidor no es confiable: se abre como zip, se leen solo los archivos de metadata
conocidos, con tope de bytes aunque el zip declare otro tamaño (un tamaño declarado es una
afirmación), y nada se extrae ni se ejecuta. Lo que no se puede leer da None, nunca una excepción.
"""
import io
import json
import struct
import zipfile

import pytest

from src.library.jarmeta import MAX_METADATA_BYTES, read_mod_metadata


def jar(files: dict[str, bytes | str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def test_a_fabric_mod() -> None:
    data = jar({"fabric.mod.json": json.dumps({"schemaVersion": 1, "id": "sodium", "name": "Sodium", "version": "0.9.2"}),
                "net/Main.class": b"\xca\xfe\xba\xbe"})
    meta = read_mod_metadata(data)
    assert meta is not None and (meta.loader, meta.mod_id, meta.name, meta.version) == ("fabric", "sodium", "Sodium", "0.9.2")


def test_a_quilt_mod() -> None:
    data = jar({"quilt.mod.json": json.dumps({"quilt_loader": {"id": "qsl", "version": "1.0",
                                                               "metadata": {"name": "Quilt Standard Libraries"}}})})
    meta = read_mod_metadata(data)
    assert meta is not None and (meta.loader, meta.mod_id, meta.name) == ("quilt", "qsl", "Quilt Standard Libraries")


@pytest.mark.parametrize("path,loader", [("META-INF/neoforge.mods.toml", "neoforge"), ("META-INF/mods.toml", "forge")])
def test_a_neoforge_or_forge_mod(path: str, loader: str) -> None:
    toml = 'modLoader="javafml"\nloaderVersion="[4,)"\n[[mods]]\nmodId="create"\ndisplayName="Create"\nversion="6.0.6"\n'
    meta = read_mod_metadata(jar({path: toml}))
    assert meta is not None and (meta.loader, meta.mod_id, meta.name, meta.version) == (loader, "create", "Create", "6.0.6")


def test_a_placeholder_version_is_not_a_version() -> None:
    toml = '[[mods]]\nmodId="create"\ndisplayName="Create"\nversion="${file.jarVersion}"\n'
    meta = read_mod_metadata(jar({"META-INF/mods.toml": toml}))
    assert meta is not None and meta.version is None


# Un paquete plantado por regla: cada uno se rechaza (None) sin lanzar.


def test_not_a_zip() -> None:
    assert read_mod_metadata(b"MZ\x90\x00 an executable, not a jar") is None


def test_a_jar_without_known_metadata() -> None:
    assert read_mod_metadata(jar({"plugin.yml": "name: Essentials", "Main.class": b"x"})) is None


def test_malformed_metadata() -> None:
    assert read_mod_metadata(jar({"fabric.mod.json": "{not json"})) is None
    assert read_mod_metadata(jar({"fabric.mod.json": json.dumps(["a", "list"])})) is None
    assert read_mod_metadata(jar({"META-INF/mods.toml": "[[mods]\nbroken"})) is None
    assert read_mod_metadata(jar({"fabric.mod.json": json.dumps({"name": "no id"})})) is None


def test_a_compression_bomb_is_not_inflated_past_the_cap() -> None:
    """Megabytes de espacios se comprimen a casi nada: leerlo entero sería inflar la bomba."""
    bomb = jar({"fabric.mod.json": " " * (MAX_METADATA_BYTES * 50) + json.dumps({"id": "x"})})
    assert len(bomb) < MAX_METADATA_BYTES
    assert read_mod_metadata(bomb) is None


def test_a_lying_declared_size_is_not_trusted() -> None:
    """El tamaño que el zip declara para un miembro es una afirmación: aquí dice 20 bytes y son
    muchos más. Leerlo no confía en ninguno de los dos números, y no lanza."""
    data = bytearray(jar({"fabric.mod.json": " " * (MAX_METADATA_BYTES * 4) + '{"id": "x"}'}))
    local = data.index(b"PK\x03\x04")
    central = data.index(b"PK\x01\x02")
    data[local + 22:local + 26] = struct.pack("<I", 20)
    data[central + 24:central + 28] = struct.pack("<I", 20)
    with zipfile.ZipFile(io.BytesIO(bytes(data))) as archive:
        assert archive.getinfo("fabric.mod.json").file_size == 20
    assert read_mod_metadata(bytes(data)) is None


def test_names_are_read_only_from_the_allowlist() -> None:
    """Un `../fabric.mod.json` o uno anidado en otra carpeta no es la metadata del jar."""
    data = jar({"../fabric.mod.json": json.dumps({"id": "escape"}),
                "nested/fabric.mod.json": json.dumps({"id": "nested"})})
    assert read_mod_metadata(data) is None
