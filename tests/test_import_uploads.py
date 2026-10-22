"""Los archivos de importación subidos esperan su trabajo en disco (O6, dueño 2026-10-21): la petición
solo los guarda y encola; el trabajo los lee, y se borran al terminar. Lo que ningún trabajo activo
sostiene, tras un reinicio o porque nunca se encoló, lo borra el barrido."""
import io
import os
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.library.import_uploads import UploadStore, read_upload
from src.library.mrpack_import import MrpackContent, MrpackError, MrpackLimits
from src.library.cf_import import CfContent
from src.library.upload_budget import UploadBudget, UploadBusy
from tests.test_modpack_import import cf_pack, mrpack, prism_zip

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


def test_an_upload_is_copied_into_the_store_and_counted_until_removed(tmp_path: Path) -> None:
    store = UploadStore(tmp_path / "imports")
    name = store.save(io.BytesIO(b"x" * 1000))
    assert store.path_of(name).read_bytes() == b"x" * 1000 and store.stored_bytes() == 1000
    store.remove(name)
    assert not store.path_of(name).exists() and store.stored_bytes() == 0
    store.remove(name)  # removing twice is harmless: a job and the sweep may both try


def test_a_name_that_is_not_one_the_store_gave_is_refused(tmp_path: Path) -> None:
    store = UploadStore(tmp_path)
    for bad in ("../secret", "/etc/passwd", "a.zip", ""):
        with pytest.raises(ValueError):
            store.path_of(bad)


def test_the_sweep_removes_what_no_active_job_holds_once_past_the_grace(tmp_path: Path) -> None:
    store = UploadStore(tmp_path)
    held, orphan, fresh = (store.save(io.BytesIO(b"abc")) for _ in range(3))
    old = (NOW - timedelta(hours=2)).timestamp()
    for name in (held, orphan):
        os.utime(store.path_of(name), (old, old))
    os.utime(store.path_of(fresh), (NOW.timestamp(), NOW.timestamp()))
    # `fresh` may be a request between storing its file and queuing its job: the grace keeps it.
    removed = store.sweep(keep={held}, now=NOW, grace=timedelta(minutes=30))
    assert removed == [orphan]
    assert store.path_of(held).exists() and store.path_of(fresh).exists() and not store.path_of(orphan).exists()


def test_the_files_kept_for_jobs_count_against_the_temporary_disk(tmp_path: Path) -> None:
    store = UploadStore(tmp_path)
    budget = UploadBudget(max_bytes=100, stored=store.stored_bytes)
    store.save(io.BytesIO(b"x" * 70))
    with pytest.raises(UploadBusy):
        with budget.reserve(40):
            pass
    with budget.reserve(30):
        assert budget.in_use == 30


def stored(store: UploadStore, data: bytes | io.BytesIO) -> str:
    return store.save(data if isinstance(data, io.BytesIO) else io.BytesIO(data))


def test_a_stored_upload_is_read_as_what_it_is(tmp_path: Path) -> None:
    store = UploadStore(tmp_path)
    kind, content = read_upload(store.path_of(stored(store, mrpack())))
    assert kind == "mrpack" and isinstance(content, MrpackContent) and content.game_version == "1.20.1"
    kind, content = read_upload(store.path_of(stored(store, prism_zip())))
    assert kind == "prism" and isinstance(content, MrpackContent) and content.name == "Aventura"
    kind, content = read_upload(store.path_of(stored(store, cf_pack())))
    assert kind == "curseforge" and isinstance(content, CfContent) and content.name == "Aventura CF"


def test_a_stored_upload_that_is_not_a_pack_says_why(tmp_path: Path) -> None:
    store = UploadStore(tmp_path)
    with pytest.raises(MrpackError):
        read_upload(store.path_of(stored(store, b"not a zip")))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("modrinth.index.json", "{}")
    big = buffer.getvalue() + b"\0" * 64
    with pytest.raises(MrpackError, match="pesa más de"):
        read_upload(store.path_of(stored(store, big)), limits=MrpackLimits(max_bytes=32, max_bytes_label="32 B"))
