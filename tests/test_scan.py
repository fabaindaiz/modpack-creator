"""Qué tiene un servidor, sin escribir en él (i-7985ee-43df2f, opción 1; i-7985ee-4364a3).

Funciones puras: qué jars hay que bajar y hashear, y cómo se compara lo instalado con un build.
Pterodactyl no da hashes (pterodactyl/panel#4894): un jar se identifica por su sha1, y solo se
vuelve a bajar si cambió su ruta, tamaño o fecha, como hace la Modrinth App.
"""
from datetime import datetime, timedelta, timezone
from typing import Any

from src.library.scan import ListedFile, ScanFile, compare, plan_scan

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
LIMIT = 1000


def listed(name: str, size: int = 10, days: int = 0) -> ListedFile:
    return ListedFile(path=f"/mods/{name}", size=size, modified_at=T0 + timedelta(days=days))


def scanned(name: str, size: int = 10, days: int = 0, **fields: Any) -> ScanFile:
    base: dict[str, Any] = {"path": f"/mods/{name}", "size": size, "modified_at": T0 + timedelta(days=days),
                            "sha1": name, "sha512": name * 2, "status": "identified",
                            "project_id": f"p-{name}", "version_id": f"v-{name}"}
    base.update(fields)
    return ScanFile(**base)


# Qué bajar.


def test_a_first_scan_hashes_every_jar() -> None:
    kept, to_hash, too_large = plan_scan([listed("a.jar"), listed("b.jar")], [], full=False, max_bytes=LIMIT)
    assert (kept, [f.path for f in to_hash], too_large) == ([], ["/mods/a.jar", "/mods/b.jar"], [])


def test_an_unchanged_jar_keeps_its_hash_and_a_changed_one_is_hashed_again() -> None:
    previous = [scanned("same.jar"), scanned("resized.jar"), scanned("touched.jar")]
    now = [listed("same.jar"), listed("resized.jar", size=11), listed("touched.jar", days=1), listed("new.jar")]
    kept, to_hash, _ = plan_scan(now, previous, full=False, max_bytes=LIMIT)
    assert [f.path for f in kept] == ["/mods/same.jar"]
    assert [f.path for f in to_hash] == ["/mods/resized.jar", "/mods/touched.jar", "/mods/new.jar"]


def test_a_full_scan_hashes_everything_again() -> None:
    """Restaurar un backup conserva la fecha vieja: la caché por fecha no lo ve
    (tarjeta derived-copy-goes-stale-silently), así que un escaneo completo no confía en ella."""
    kept, to_hash, _ = plan_scan([listed("same.jar")], [scanned("same.jar")], full=True, max_bytes=LIMIT)
    assert (kept, [f.path for f in to_hash]) == ([], ["/mods/same.jar"])


def test_a_jar_past_the_size_limit_is_not_downloaded() -> None:
    """Un jar del servidor no es confiable: se hashea, nunca se carga, y no se baja sin límite
    (tarjeta untrusted-package-is-parsed-never-loaded)."""
    kept, to_hash, too_large = plan_scan([listed("huge.jar", size=LIMIT + 1)], [], full=False, max_bytes=LIMIT)
    assert (kept, to_hash) == ([], [])
    assert [(f.path, f.status) for f in too_large] == [("/mods/huge.jar", "too_large")]


def test_a_jar_that_was_unknown_keeps_its_hash_and_is_asked_about_again() -> None:
    """Modrinth puede conocer mañana un jar que hoy no: se le vuelve a preguntar, con el hash que
    ya se tiene, sin bajarlo de nuevo."""
    unknown = scanned("mystery.jar", status="unknown", project_id=None, version_id=None)
    kept, to_hash, _ = plan_scan([listed("mystery.jar")], [unknown], full=False, max_bytes=LIMIT)
    assert to_hash == []
    assert [(f.path, f.status, f.sha1) for f in kept] == [("/mods/mystery.jar", "unknown", "mystery.jar")]


# Comparar con un build.


def lock_entry(project: str, version: str, server: str = "include") -> dict[str, Any]:
    return {"project_id": project, "version_id": version, "server": server}


def test_comparing_says_what_matches_differs_is_missing_extra_or_unknown() -> None:
    files = [scanned("a.jar", project_id="A", version_id="a1"),
             scanned("b.jar", project_id="B", version_id="b-old"),
             scanned("x.jar", project_id="X", version_id="x1"),
             scanned("hand.jar", status="unknown", project_id=None, version_id=None)]
    lock = [lock_entry("A", "a1"), lock_entry("B", "b-new"), lock_entry("C", "c1"),
            lock_entry("client-only", "z1", server="exclude")]
    result = compare(files, lock)
    assert [f.path for f in result.matching] == ["/mods/a.jar"]
    assert [(f.path, e.version_id) for f, e in result.different_version] == [("/mods/b.jar", "b-new")]
    assert [e.project_id for e in result.missing] == ["C"]
    assert [f.path for f in result.extra] == ["/mods/x.jar"]
    assert [f.path for f in result.unknown] == ["/mods/hand.jar"]
    assert not result.in_sync


def test_a_second_copy_of_a_mod_is_extra() -> None:
    files = [scanned("a.jar", project_id="A", version_id="a1"), scanned("a-copy.jar", project_id="A", version_id="a1")]
    result = compare(files, [lock_entry("A", "a1")])
    assert [f.path for f in result.matching] == ["/mods/a.jar"]
    assert [f.path for f in result.extra] == ["/mods/a-copy.jar"]


def test_a_server_exactly_on_the_build_is_in_sync() -> None:
    assert compare([scanned("a.jar", project_id="A", version_id="a1")], [lock_entry("A", "a1")]).in_sync


def test_a_time_read_back_from_mongodb_is_the_same_key() -> None:
    """MongoDB devuelve las fechas sin zona y truncadas a milisegundos; el panel las manda con zona.
    Si la clave no las normaliza, ningún jar coincide y cada escaneo baja todo."""
    from_panel = ListedFile(path="/mods/a.jar", size=1, modified_at=datetime(2026, 9, 1, 12, 0, 0, 123456, tzinfo=timezone.utc))
    from_mongo = ListedFile(path="/mods/a.jar", size=1, modified_at=datetime(2026, 9, 1, 12, 0, 0, 123000))
    assert from_panel.key() == from_mongo.key()


# Archivos locales del bundle (d-7985ee-c8a15d): un jar que no está en Modrinth, registrado por su
# sha512, explica ese archivo en el servidor.


def test_a_local_file_of_the_build_explains_an_unknown_jar() -> None:
    from src.library.scan import LocalFile

    private = scanned("private.jar", status="unknown", project_id=None, version_id=None, sha512="p" * 128)
    stranger = scanned("stranger.jar", status="unknown", project_id=None, version_id=None, sha512="s" * 128)
    local = LocalFile(filename="private.jar", sha512="p" * 128, sha1="p" * 40, size=10)
    result = compare([private, stranger], [], local_files=[local])
    assert [f.path for f in result.local] == ["/mods/private.jar"]
    assert [f.path for f in result.unknown] == ["/mods/stranger.jar"]
    assert compare([private], [], local_files=[local]).in_sync
