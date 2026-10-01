"""Exportar un build como .mrpack para los jugadores (i-7985ee-7352b3).

Funciones puras. El formato es el de la especificación de Modrinth
(support.modrinth.com/en/articles/8802351): `modrinth.index.json` con `formatVersion` 1, archivos
con sha1 y sha512, descargas solo de cuatro dominios, rutas relativas sin `..`. Cada export se
valida contra esa especificación antes de servirse (tarjeta validate-each-transformation-run).
"""
import io
import json
import zipfile
from typing import Any

import pytest

from src.library.mrpack import client_env, fabric_loader_version, mrpack_index, quilt_loader_version, validate_index, write_mrpack
from src.library.resolution import CandidateFile, LockEntry, Server


def entry(project: str, *, type: str = "mod", env: str | None = "client_and_server", server: Server = "include",
          url: str | None = None, filename: str | None = None) -> LockEntry:
    name = filename or f"{project}.jar"
    return LockEntry(project_id=project, version_id=f"v-{project}", version_number="1", project_type=type,
                     environment=env, server=server, reason="en el pack", requested_by=[],
                     file=CandidateFile(filename=name, url=url or f"https://cdn.modrinth.com/data/{project}/{name}",
                                        sha1="a" * 40, sha512="b" * 128, size=10))


def index(*entries: LockEntry, loader: str = "fabric", loader_version: str = "0.19.5") -> dict[str, Any]:
    return mrpack_index(name="Pack", version_id="3", summary="build 3", game_version="26.3",
                        loader=loader, loader_version=loader_version, entries=list(entries))


# El índice.


def test_the_index_has_what_the_spec_requires() -> None:
    built = index(entry("sodium", env="client_only", server="exclude"))
    assert {k: built[k] for k in ("formatVersion", "game", "versionId", "name", "summary", "dependencies")} == {
        "formatVersion": 1, "game": "minecraft", "versionId": "3", "name": "Pack", "summary": "build 3",
        "dependencies": {"minecraft": "26.3", "fabric-loader": "0.19.5"}}
    [file] = built["files"]
    assert file == {"path": "mods/sodium.jar", "hashes": {"sha1": "a" * 40, "sha512": "b" * 128},
                    "env": {"client": "required", "server": "unsupported"},
                    "downloads": ["https://cdn.modrinth.com/data/sodium/sodium.jar"], "fileSize": 10}
    assert validate_index(built) == []


@pytest.mark.parametrize("loader,key", [("fabric", "fabric-loader"), ("quilt", "quilt-loader"),
                                        ("neoforge", "neoforge"), ("forge", "forge")])
def test_each_loader_has_its_dependency_key(loader: str, key: str) -> None:
    assert index(loader=loader, loader_version="1.0")["dependencies"] == {"minecraft": "26.3", key: "1.0"}


def test_each_type_goes_to_its_folder() -> None:
    built = index(entry("m"), entry("s", type="shader", env=None, server="exclude", filename="s.zip"),
                  entry("r", type="resourcepack", env=None, server="exclude", filename="r.zip"),
                  entry("d", type="datapack", env=None, filename="d.zip"))
    assert [f["path"] for f in built["files"]] == ["mods/m.jar", "shaderpacks/s.zip", "resourcepacks/r.zip", "datapacks/d.zip"]


def test_the_server_side_is_the_builds_decision_and_the_client_side_comes_from_modrinth() -> None:
    assert client_env("client_only", "mod") == "required"
    assert client_env("server_only", "mod") == "unsupported"
    assert client_env("dedicated_server_only", "mod") == "unsupported"
    assert client_env("server_only_client_optional", "mod") == "optional"
    assert client_env("client_or_server", "mod") == "optional"
    assert client_env("client_or_server_prefers_both", "mod") == "required"
    # Sin environment, no se supone nada: el jugador lo instala, como hace un launcher sin `env`.
    assert client_env(None, "mod") == "required"
    assert client_env("unknown", "mod") == "required"
    assert client_env(None, "shader") == "required"
    assert client_env(None, "datapack") == "unsupported"
    [file] = index(entry("lithium", env="client_or_server_prefers_both", server="include"))["files"]
    assert file["env"] == {"client": "required", "server": "required"}


# Validación (validate-each-transformation-run).


def test_a_download_outside_the_four_domains_is_refused() -> None:
    assert validate_index(index(entry("a", url="https://github.com/o/r/releases/a.jar"))) == []
    problems = validate_index(index(entry("a", url="https://example.org/a.jar")))
    assert problems and "example.org" in problems[0]


def test_an_http_url_or_one_with_spaces_is_refused() -> None:
    assert validate_index(index(entry("a", url="http://cdn.modrinth.com/a.jar")))
    assert validate_index(index(entry("a", url="https://cdn.modrinth.com/a b.jar")))


@pytest.mark.parametrize("filename", ["../evil.jar", "/abs.jar", "C:/x.jar", "a\\..\\b.jar"])
def test_a_path_that_escapes_the_instance_is_refused(filename: str) -> None:
    built = index(entry("a"))
    built["files"][0]["path"] = filename
    assert validate_index(built)


def test_missing_hashes_or_an_unknown_dependency_key_are_refused() -> None:
    built = index(entry("a"))
    del built["files"][0]["hashes"]["sha512"]
    assert validate_index(built)
    built = index(entry("a"))
    built["dependencies"]["liteloader"] = "1"
    assert validate_index(built)


def test_the_archive_holds_the_index_at_its_root() -> None:
    data = write_mrpack(index(entry("a")))
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.namelist() == ["modrinth.index.json"]
        assert json.loads(archive.read("modrinth.index.json"))["formatVersion"] == 1


def test_an_invalid_index_is_never_written() -> None:
    with pytest.raises(ValueError, match="example.org"):
        write_mrpack(index(entry("a", url="https://example.org/a.jar")))


# La versión del loader, con respuestas con la forma real de cada meta (2026-10-01).


def test_fabric_takes_the_first_stable_loader() -> None:
    answer = [{"loader": {"version": "0.19.6", "stable": False}}, {"loader": {"version": "0.19.5", "stable": True}},
              {"loader": {"version": "0.19.4", "stable": False}}]
    assert fabric_loader_version(answer) == "0.19.5"
    assert fabric_loader_version([]) is None


def test_quilt_sorts_by_version_and_skips_betas() -> None:
    """La meta de Quilt no ordena ni filtra (investigación §2)."""
    answer = [{"loader": {"version": v}} for v in ["0.20.0-beta.9", "0.20.0-beta.7", "0.24.0", "0.9.1", "0.20.2-beta.1", "0.23.1"]]
    assert quilt_loader_version(answer) == "0.24.0"
    assert quilt_loader_version([{"loader": {"version": "0.1.0-beta.1"}}]) is None
