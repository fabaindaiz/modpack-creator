"""Importar un modpack (spec 2026-10-09-importar-modpacks): un .mrpack subido o uno publicado en
Modrinth entra como pack nuevo o reemplaza el contenido de uno existente. Contra MongoDB real;
Modrinth y el CDN son dobles que anotan cada llamada (el dueño cuenta cada request)."""
import hashlib
import io
import json
import zipfile
from typing import Any, Optional, Sequence, cast

import pytest
from fastapi import HTTPException

from src.database.modpack import ModpackDatabase
from src.database.modrinth import ModrinthDatabase
from src.library.api.client.download import TooLarge
from src.model.modpack import ModpackBundle, PackProject, ProjectOrigin
from src.model.modrinth import ModrinthProject, ModrinthVersion
from src.model.utils import MinecraftLoaders
from src.services.modpack import ModpackService
from src.services.modpack_import import ModpackImportService
from tests._actors import EDITOR, RecordingAudit
from tests.conftest import MongoRunner
from tests.test_builds import FakeLoaders

pytestmark = pytest.mark.network


def sha(kind: str, text: str) -> str:
    return hashlib.new(kind, text.encode()).hexdigest()


def project(project_id: str, title: str, project_type: str = "mod") -> dict[str, Any]:
    return dict(id=project_id, slug=title.lower().replace(" ", "-"), team="t", title=title, description="", body="",
                project_type=project_type, status="approved", downloads=1, followers=1, categories=[],
                additional_categories=[], versions=[], game_versions=["1.20.1"], loaders=["fabric"],
                issues_url=None, source_url=None, wiki_url=None, icon_url=None,
                published="2020-01-01T00:00:00Z", updated="2020-01-01T00:00:00Z")


def version(version_id: str, project_id: str, content: str, kind: str = "release", day: int = 1,
            url: Optional[str] = None, filename: Optional[str] = None) -> dict[str, Any]:
    return dict(id=version_id, project_id=project_id, author_id="a", name=version_id, version_number=version_id,
                version_type=kind, status="listed", downloads=1, game_versions=["1.20.1"], loaders=["fabric"],
                featured=False, date_published=f"2026-09-{day:02d}T00:00:00Z", dependencies=[], changelog=None,
                files=[{"hashes": {"sha1": sha("sha1", content), "sha512": sha("sha512", content)},
                        "url": url or f"https://cdn.modrinth.com/data/{project_id}/{version_id}.jar",
                        "filename": filename or f"{version_id}.jar", "primary": True, "size": len(content)}])


SODIUM = ("AANobbMI", "Sodium", "sodium jar")
LITHIUM = ("gvQqBUqZ", "Lithium", "lithium jar")
LIB = ("Ha28R6CL", "Beta Lib", "lib jar")


class FakeModrinth:
    def __init__(self) -> None:
        self.projects = {p[0]: project(p[0], p[1]) for p in (SODIUM, LITHIUM, LIB)}
        self.versions = [version("sod-1", SODIUM[0], SODIUM[2]), version("lit-1", LITHIUM[0], LITHIUM[2]),
                         version("lib-b", LIB[0], LIB[2], kind="beta")]
        self.calls: list[tuple[str, Any]] = []

    async def files_hash_post_many(self, hashes: list[str], algorithm: str = "sha512") -> dict[str, ModrinthVersion]:
        self.calls.append(("files_hash_post_many", (algorithm, tuple(sorted(hashes)))))
        found = {}
        for v in self.versions:
            for f in v["files"]:
                if f["hashes"][algorithm] in hashes:
                    found[f["hashes"][algorithm]] = ModrinthVersion.parse(**json.loads(json.dumps(v)))
        return found

    async def project_get_many(self, slugs: list[str]) -> list[ModrinthProject]:
        self.calls.append(("project_get_many", tuple(slugs)))
        return [ModrinthProject.parse(**p) for p in self.projects.values() if p["id"] in slugs or p["slug"] in slugs]

    async def project_get(self, slug: str) -> ModrinthProject:
        self.calls.append(("project_get", slug))
        found = next((p for p in self.projects.values() if slug in (p["id"], p["slug"])), None)
        if found is None:
            raise HTTPException(status_code=404, detail="not found")
        return ModrinthProject.parse(**found)

    async def project_versions(self, slug: str, loaders: Sequence[str] = (), game_versions: Sequence[str] = (),
                               featured: Optional[bool] = None) -> list[ModrinthVersion]:
        self.calls.append(("project_versions", slug))
        project_id = next(p["id"] for p in self.projects.values() if slug in (p["id"], p["slug"]))
        return [ModrinthVersion.parse(**v) for v in self.versions if v["project_id"] == project_id]

    async def version_get_many(self, ids: list[str]) -> list[ModrinthVersion]:
        self.calls.append(("version_get_many", tuple(ids)))
        return [ModrinthVersion.parse(**v) for v in self.versions if v["id"] in ids]

    async def version_get(self, id: str) -> ModrinthVersion:
        self.calls.append(("version_get", id))
        found = next((v for v in self.versions if v["id"] == id), None)
        if found is None:
            raise HTTPException(status_code=404, detail="not found")
        return ModrinthVersion.parse(**found)


class FakeDownloader:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.calls: list[tuple[str, int]] = []

    async def download_capped(self, url: str, max_bytes: int) -> bytes:
        self.calls.append((url, max_bytes))
        data = self.files[url]
        if len(data) > max_bytes:
            raise TooLarge("too large")
        return data


def mrpack(game: str = "1.20.1", loader: str = "fabric-loader", extra_files: Sequence[dict[str, Any]] = (),
           members: Optional[dict[str, bytes]] = None) -> bytes:
    def entry(path: str, content: str, server: Optional[str] = None) -> dict[str, Any]:
        item: dict[str, Any] = {"path": path, "hashes": {"sha1": sha("sha1", content), "sha512": sha("sha512", content)},
                                "fileSize": len(content), "downloads": ["https://cdn.modrinth.com/x"]}
        if server is not None:
            item["env"] = {"client": "required", "server": server}
        return item

    index = {"formatVersion": 1, "game": "minecraft", "versionId": "2.0", "name": "Aventura",
             "dependencies": {"minecraft": game, loader: "0.16.10"},
             "files": [entry("mods/sodium.jar", SODIUM[2], "unsupported"), entry("mods/lithium.jar", LITHIUM[2], "required"),
                       entry("mods/lib.jar", LIB[2]), entry("mods/private.jar", "not on modrinth"), *extra_files]}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("modrinth.index.json", json.dumps(index))
        for name, content in (members or {"overrides/config/a.toml": b"", "overrides/mods/own.jar": b"own"}).items():
            archive.writestr(name, content)
    return buffer.getvalue()


class Scenario:
    def __init__(self) -> None:
        self.api = FakeModrinth()
        self.downloader = FakeDownloader()
        self.audit = RecordingAudit()
        self.modpackDB = ModpackDatabase()
        modpack = ModpackService(modpackDB=self.modpackDB, modrinthDB=ModrinthDatabase(), modrinthAPI=cast(Any, self.api),
                                 loaderAPI=cast(Any, FakeLoaders()), audit=self.audit)
        self.service = ModpackImportService(modpackService=modpack, modpackDB=self.modpackDB, modrinthDB=ModrinthDatabase(),
                                            modrinthAPI=cast(Any, self.api), downloader=cast(Any, self.downloader),
                                            audit=self.audit)

    async def bundle(self, version: str = "1.20.1", loader: MinecraftLoaders = MinecraftLoaders.fabric) -> str:
        created = await self.modpackDB.bundle_insert(ModpackBundle(
            name="Mío", description="", version=version, loader=loader,
            projects=[PackProject(project_id="OLDPACK1", origin=ProjectOrigin.pack),
                      PackProject(project_id="ADMIN001", origin=ProjectOrigin.admin)]))
        return str(created.id)


def test_a_new_pack_from_an_mrpack_takes_its_target_its_mods_and_what_they_run_on(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        s = Scenario()
        report = await s.service.run_mrpack(mrpack(), EDITOR)
        bundle = await s.modpackDB.bundle_get(report.bundle_id)
        return report, bundle, s.api.calls, [e[0] for e in s.audit.events]

    report, bundle, calls, events = mongo(scenario)
    assert report.created is True
    assert (bundle.name, bundle.version, bundle.loader, bundle.loader_version) == ("Aventura", "1.20.1", MinecraftLoaders.fabric, "0.16.10")
    assert {p.project_id: p.origin for p in bundle.projects} == {i: ProjectOrigin.pack for i in (SODIUM[0], LITHIUM[0], LIB[0])}
    assert report.unknown == ["mods/private.jar", "overrides/mods/own.jar"]
    assert report.overrides == {"overrides/config": 1}
    # env.server seeds the decision; a beta outside the pack's channels, the channel exception.
    assert bundle.server_decisions == {SODIUM[0]: False, LITHIUM[0]: True}
    assert bundle.channel_exceptions == {LIB[0]: "beta"}
    assert (report.decisions, report.channel_exceptions) == (bundle.server_decisions, bundle.channel_exceptions)
    # One identification call for every hash, then one call for the projects: nothing per mod.
    assert [c[0] for c in calls] == ["files_hash_post_many", "project_get_many"]
    assert calls[0][1][0] == "sha512" and len(calls[0][1][1]) == 5
    assert "bundle.create" in events and "bundle.project.replace" in events


def test_hashes_the_catalog_already_has_are_not_asked_again(mongo: MongoRunner) -> None:
    async def scenario() -> list[tuple[str, Any]]:
        s = Scenario()
        await ModrinthDatabase().version_upsert_many([ModrinthVersion.parse(**v) for v in s.api.versions])
        await s.service.run_mrpack(mrpack(), EDITOR)
        return [c for c in s.api.calls if c[0] == "files_hash_post_many"]

    [(_, (algorithm, asked))] = mongo(scenario)
    assert algorithm == "sha512" and set(asked) == {sha("sha512", "not on modrinth"), sha("sha512", "own")}


def test_replacing_the_content_keeps_what_the_admin_added(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, dict[str, Any]]:
        s = Scenario()
        bundle_id = await s.bundle()
        await s.modpackDB.bundle_decision_set(bundle_id, SODIUM[0], True)
        report = await s.service.run_mrpack(mrpack(), EDITOR, into=bundle_id)
        bundle = await s.modpackDB.bundle_get(bundle_id)
        return report, {p.project_id: p.origin for p in bundle.projects} | {"decisions": bundle.server_decisions}

    report, after = mongo(scenario)
    assert report.created is False
    assert after == {"ADMIN001": ProjectOrigin.admin, SODIUM[0]: ProjectOrigin.pack, LITHIUM[0]: ProjectOrigin.pack,
                     LIB[0]: ProjectOrigin.pack, "decisions": {SODIUM[0]: True, LITHIUM[0]: True}}


@pytest.mark.parametrize("game, loader", [("1.21.1", "fabric-loader"), ("1.20.1", "neoforge")])
def test_another_target_than_the_pack_is_refused_and_changes_nothing(mongo: MongoRunner, game: str, loader: str) -> None:
    async def scenario() -> tuple[int, str, list[str], list[Any]]:
        s = Scenario()
        bundle_id = await s.bundle()
        with pytest.raises(HTTPException) as refused:
            await s.service.run_mrpack(mrpack(game=game, loader=loader), EDITOR, into=bundle_id)
        bundle = await s.modpackDB.bundle_get(bundle_id)
        return refused.value.status_code, str(refused.value.detail), [p.project_id for p in bundle.projects], s.api.calls

    status, detail, projects, calls = mongo(scenario)
    assert status == 409 and "1.20.1" in detail and projects == ["OLDPACK1", "ADMIN001"] and calls == []


def test_a_broken_mrpack_is_a_422_with_its_reason_and_nothing_is_created(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[int, str, int]:
        s = Scenario()
        with pytest.raises(HTTPException) as refused:
            await s.service.run_mrpack(b"not a zip", EDITOR)
        return refused.value.status_code, str(refused.value.detail), await ModpackBundle.find().count()

    status, detail, bundles = mongo(scenario)
    assert status == 422 and "no es un .mrpack" in detail and bundles == 0


# Desde Modrinth: un modpack publicado, por URL, slug o id de versión.

PACK = ("PACK0001", "Aventura Pack")


def published(s: Scenario, *versions: tuple[str, int, str]) -> None:
    """The modpack project, with versions (id, day, file host); each file is the .mrpack."""
    s.api.projects[PACK[0]] = project(PACK[0], PACK[1], project_type="modpack")
    data = mrpack()
    for version_id, day, host in versions:
        url = f"https://{host}/data/{PACK[0]}/versions/{version_id}/pack.mrpack"
        entry = version(version_id, PACK[0], "unused", day=day, url=url, filename="pack.mrpack")
        entry["files"][0]["hashes"] = {"sha1": hashlib.sha1(data).hexdigest(), "sha512": hashlib.sha512(data).hexdigest()}
        s.api.versions.append(entry)
        s.downloader.files[url] = data


@pytest.mark.parametrize("ref", ["https://modrinth.com/modpack/aventura-pack", "aventura-pack", PACK[0]])
def test_a_published_modpack_imports_its_newest_version(mongo: MongoRunner, ref: str) -> None:
    async def scenario() -> tuple[Any, ...]:
        s = Scenario()
        published(s, ("v1", 1, "cdn.modrinth.com"), ("v2", 2, "cdn.modrinth.com"))
        report = await s.service.run_modrinth(ref, EDITOR)
        return report, s.downloader.calls, [c[0] for c in s.api.calls]

    report, downloads, calls = mongo(scenario)
    assert report.created and report.source == "Aventura Pack v2"
    assert downloads == [(f"https://cdn.modrinth.com/data/{PACK[0]}/versions/v2/pack.mrpack", 50 * 1024 * 1024)]
    assert calls[:2] == ["project_get", "project_versions"]


def test_a_version_url_imports_that_version(mongo: MongoRunner) -> None:
    async def scenario() -> str:
        s = Scenario()
        published(s, ("v1", 1, "cdn.modrinth.com"), ("v2", 2, "cdn.modrinth.com"))
        return (await s.service.run_modrinth("https://modrinth.com/modpack/aventura-pack/version/v1", EDITOR)).source

    assert mongo(scenario) == "Aventura Pack v1"


def test_a_version_id_alone_imports_that_version(mongo: MongoRunner) -> None:
    async def scenario() -> str:
        s = Scenario()
        published(s, ("v1", 1, "cdn.modrinth.com"))
        return (await s.service.run_modrinth("v1", EDITOR)).source

    assert mongo(scenario) == "Aventura Pack v1"


@pytest.mark.parametrize("setup, reason", [
    ("not_a_modpack", "no es un modpack"),
    ("other_host", "cdn.modrinth.com"),
    ("altered", "no coincide"),
    ("unknown", "no conoce"),
    ("too_large", "50 MiB"),
    ("bad_url", "no es de Modrinth"),
])
def test_a_published_modpack_is_refused_with_its_reason(mongo: MongoRunner, setup: str, reason: str) -> None:
    async def scenario() -> tuple[int, str, int]:
        s = Scenario()
        published(s, ("v1", 1, "evil.example" if setup == "other_host" else "cdn.modrinth.com"))
        ref = "aventura-pack"
        if setup == "not_a_modpack":
            ref = "sodium"
        elif setup == "altered":
            s.downloader.files[next(iter(s.downloader.files))] = b"another file"
        elif setup == "unknown":
            ref = "nadie-lo-tiene"
        elif setup == "too_large":
            s.downloader.files[next(iter(s.downloader.files))] = b"x" * (50 * 1024 * 1024 + 1)
        elif setup == "bad_url":
            ref = "https://evil.example/modpack/aventura-pack"
        with pytest.raises(HTTPException) as refused:
            await s.service.run_modrinth(ref, EDITOR)
        return refused.value.status_code, str(refused.value.detail), await ModpackBundle.find().count()

    status, detail, bundles = mongo(scenario)
    assert status == 422 and reason in detail and bundles == 0



# Como trabajo (spec 2026-10-09-economia-de-requests, E7): leer el archivo y validar el destino es
# inmediato; lo que pide a Modrinth va en un trabajo, uno por persona a la vez, con su informe.


async def run_jobs(s: Scenario) -> None:
    from src.database.jobs import JobDatabase
    from src.services.jobs import JobWorker

    worker = JobWorker(JobDatabase(), s.service.handlers(), owner="w1")
    while await worker.run_once():
        pass


def test_an_upload_is_read_at_once_and_identified_in_a_job_with_its_report(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        s = Scenario()
        job = await s.service.import_mrpack(mrpack(), EDITOR)
        again = await s.service.import_mrpack(mrpack(), EDITOR)
        before = list(s.api.calls)
        await run_jobs(s)
        done, report = await s.service.import_status(str(job.id))
        return job, again, before, done, report

    job, again, before, done, report = mongo(scenario)
    # The identity is iss + sub (AGENTS.md), never sub alone.
    assert (job.kind, job.key) == ("modpack_import", f"import:{EDITOR.iss}|{EDITOR.sub}") and again.id == job.id
    assert before == []  # nothing asked of Modrinth before the job
    assert done.status == "done" and report is not None and report.created
    assert report.unknown == ["mods/private.jar", "overrides/mods/own.jar"] and len(report.projects) == 3


@pytest.mark.parametrize("data, into_other, status", [(b"not a zip", False, 422), (None, True, 409)])
def test_a_refusal_known_without_modrinth_comes_at_once_without_a_job(
        mongo: MongoRunner, data: Optional[bytes], into_other: bool, status: int) -> None:
    async def scenario() -> tuple[int, int]:
        s = Scenario()
        into = await s.bundle(version="1.21.1") if into_other else None
        with pytest.raises(HTTPException) as refused:
            await s.service.import_mrpack(data if data is not None else mrpack(), EDITOR, into=into)
        from src.model.jobs import Job
        return refused.value.status_code, await Job.find().count()

    assert mongo(scenario) == (status, 0)


def test_a_modrinth_reference_is_imported_by_a_job(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        s = Scenario()
        published(s, ("v1", 1, "cdn.modrinth.com"), ("v2", 2, "cdn.modrinth.com"))
        with pytest.raises(HTTPException) as bad:
            await s.service.import_modrinth("https://evil.example/modpack/x", EDITOR)
        job = await s.service.import_modrinth("aventura-pack", EDITOR)
        before = list(s.api.calls)
        await run_jobs(s)
        return bad.value.status_code, before, await s.service.import_status(str(job.id))

    status, before, (done, report) = mongo(scenario)
    assert status == 422 and before == [] and done.status == "done" and report.source == "Aventura Pack v2"


def test_a_job_that_modrinth_refuses_ends_failed_with_the_reason(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, Any]:
        s = Scenario()
        job = await s.service.import_modrinth("nadie-lo-tiene", EDITOR)
        await run_jobs(s)
        return await s.service.import_status(str(job.id))

    done, report = mongo(scenario)
    assert done.status == "failed" and "no conoce" in (done.error or "") and report is None


def prism_zip() -> io.BytesIO:
    """A Prism instance for 1.20.1 fabric: Sodium on, Lithium turned off, Beta Lib only named by its
    packwiz metafile, and a jar Modrinth does not know."""
    buffer = io.BytesIO()
    pack = {"formatVersion": 1, "components": [{"uid": "net.minecraft", "version": "1.20.1"},
                                               {"uid": "net.fabricmc.fabric-loader", "version": "0.16.10"}]}
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("instance.cfg", "name=Aventura\n")
        archive.writestr("mmc-pack.json", json.dumps(pack))
        archive.writestr("minecraft/mods/sodium.jar", SODIUM[2])
        archive.writestr("minecraft/mods/lithium.jar.disabled", LITHIUM[2])
        archive.writestr("minecraft/mods/private.jar", "not on modrinth")
        archive.writestr("minecraft/mods/.index/lib.pw.toml",
                         'name = "Beta Lib"\nfilename = "lib.jar"\n\n[update.modrinth]\nmod-id = "Ha28R6CL"\nversion = "libb"\n')
        archive.writestr("minecraft/saves/Mundo/level.dat", b"world")
    buffer.seek(0)
    return buffer


def test_a_prism_instance_imports_as_a_job_with_its_disabled_mods_off_and_its_hints(mongo: MongoRunner) -> None:
    # T6 (owner, 2026-10-09): P2 a .jar.disabled comes in «Desactivado»; P4 a .pw.toml whose jar is
    # not in the zip names its Modrinth version.
    async def scenario() -> tuple[Any, ...]:
        s = Scenario()
        s.api.versions.append(version("libb", LIB[0], "lib jar 2", kind="beta"))
        job = await s.service.import_prism(prism_zip(), EDITOR)
        await run_jobs(s)
        done, report = await s.service.import_status(str(job.id))
        bundle = await s.modpackDB.bundle_get(report.bundle_id) if report else None
        return done, report, bundle

    done, report, bundle = mongo(scenario)
    assert done.status == "done" and report is not None and bundle is not None
    assert (bundle.name, bundle.version, bundle.loader) == ("Aventura", "1.20.1", MinecraftLoaders.fabric)
    assert sorted(report.projects) == sorted([SODIUM[0], LITHIUM[0], LIB[0]])
    assert bundle.server_decisions == {LITHIUM[0]: False} and report.disabled == 1
    assert report.unknown == ["minecraft/mods/private.jar"] and report.overrides == {"minecraft/saves": 1}
