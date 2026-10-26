"""Importar un modpack desde el panel (spec 2026-10-09-importar-modpacks): la página, el formulario
en el detalle de un pack, la subida con su tope y el informe. El servicio es un doble."""
import io
import re
from typing import Any, Optional

import pytest
from dependency_injector import providers

from datetime import datetime, timezone

from beanie import PydanticObjectId

from src.exceptions import HTTP_409_CONFLICT, HTTP_422_UNPROCESSABLE_ENTITY
from src.model.jobs import Job
from src.model.users import Actor
from src.services.modpack_import import ImportReport
from tests._actors import EDITOR
from tests.test_web_modpacks import BUNDLE_ID, HTMX, setup_all, visible

UPLOAD_LIMIT = 1024 * 1024 * 1024  # a Prism zip (T6/P1); a .mrpack is still 50 MiB once read


class FakeImport:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.error: Optional[Exception] = None
        self.report = ImportReport(
            bundle_id=str(BUNDLE_ID), created=True, source="Aventura 2.0", projects=["P1", "P2", "P3"],
            unknown=["mods/privado.jar", "overrides/mods/propio.jar"], overrides={"overrides/config": 31, "overrides/kubejs": 6},
            decisions={"P1": False}, channel_exceptions={"P3": "beta"})
        self.job = Job.model_construct(
            id=PydanticObjectId(), kind="modpack_import", key="import:x", status="queued", active=True, payload={},
            progress=None, waiting_until=None, updated_at=None, created_at=datetime.now(timezone.utc), attempts=0,
            max_attempts=1, error=None, requested_by=EDITOR.ref(), checkpoint={}, step=None)

    async def import_upload(self, file: Any, filename: str, actor: Actor, into: Optional[str] = None) -> Job:
        # O6: the route hands over the file as it came, unread; the job tells what it is.
        self.calls.append(("import_upload", file.read(), filename, actor, into))
        if self.error:
            raise self.error
        return self.job

    curseforge_enabled = False

    async def import_curseforge_code(self, code: str, actor: Actor, into: Optional[str] = None) -> Job:
        self.calls.append(("import_curseforge_code", code, actor, into))
        return self.job

    async def import_modrinth(self, ref: str, actor: Actor, into: Optional[str] = None) -> Job:
        self.calls.append(("import_modrinth", ref, actor, into))
        if self.error:
            raise self.error
        return self.job

    async def import_status(self, job_id: str) -> tuple[Job, Optional[ImportReport]]:
        return self.job, self.report if self.job.status == "done" else None

    last: bool = False

    async def last_import(self, actor: Actor) -> Optional[tuple[Job, Optional[ImportReport]]]:
        if not self.last:
            return None
        return self.job, self.report if self.job.status == "done" else None


@pytest.fixture(autouse=True)
def reset_overrides() -> Any:
    yield
    import main

    for provider in (main.container.modpackService, main.container.modrinthService, main.container.updateCheckService,
                     main.container.pterodactylService, main.container.modpackImportService):
        provider.reset_override()


def setup() -> tuple[Any, FakeImport]:
    import main

    client, _, _, _, _ = setup_all(EDITOR)
    fake = FakeImport()
    main.container.modpackImportService.override(providers.Object(fake))
    return client, fake


def test_the_list_links_to_the_import_page_and_the_page_offers_a_file_and_a_modrinth_reference() -> None:
    client, _ = setup()
    assert 'href="/modpacks/importar"' in client.get("/modpacks").text
    text = client.get("/modpacks/importar").text
    assert 'hx-post="/modpacks/importar"' in text and 'hx-encoding="multipart/form-data"' in text
    assert 'type="file"' in text and 'name="archivo"' in text and 'accept=".mrpack,.zip"' in text
    assert 'name="ref"' in text and 'type="hidden" name="into"' not in text


def test_the_pack_links_to_the_import_page_instead_of_its_own_form() -> None:
    """O9 D5 (owner, 2026-10-26): «Importar modpack» is the one place to import; the pack links to
    it with itself chosen, and says what stays."""
    client, _ = setup()
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    section = text[text.index('id="import-section"'):]
    section = section[:section.index("</section>")] if "</section>" in section else section[:2000]
    assert f'href="/modpacks/importar?en={BUNDLE_ID}"' in section and "Reemplazar con otro pack" in visible(section)
    assert 'name="archivo"' not in section and 'name="ref"' not in section
    assert "lo que agregaste a mano se queda" in visible(section)


def test_an_uploaded_mrpack_queues_its_import_and_shows_it_until_the_report() -> None:
    client, fake = setup()
    response = client.post("/modpacks/importar", headers=HTMX, files={"archivo": ("pack.mrpack", b"PK data", "application/zip")})
    assert response.status_code == 200 and response.headers["HX-Trigger"] == "job-started"
    assert fake.calls == [("import_upload", b"PK data", "pack.mrpack", EDITOR, None)]
    assert "Importación de modpack" in response.text and 'hx-trigger="every 2s"' in response.text
    assert f'hx-get="/modpacks/importar/{fake.job.id}"' in response.text
    fake.job.status, fake.job.active = "done", False
    response = client.get(f"/modpacks/importar/{fake.job.id}", headers=HTMX)
    assert "every 2s" not in response.text
    shown = " ".join(visible(response.text).split())
    assert "Importado: 3 mods de Aventura 2.0" in shown
    assert "No están en Modrinth (2)" in shown and "mods/privado.jar" in shown and "overrides/mods/propio.jar" in shown
    assert "Súbelos al servidor a mano; al importar el servidor quedan como archivos locales." in shown
    assert "Marcados «En el servidor»: 0 · Aceptan beta: 1" in shown  # B5: P1 was marked off, P3 takes betas
    assert "Overrides sin importar: 37 archivos" in shown and "config/ (31)" in shown and "kubejs/ (6)" in shown
    assert f'href="/modpacks/{BUNDLE_ID}"' in response.text


def test_a_modrinth_reference_is_imported_into_the_pack_it_came_from() -> None:
    client, fake = setup()
    fake.report.created = False
    response = client.post("/modpacks/importar", headers=HTMX,
                           data={"ref": "https://modrinth.com/modpack/aventura", "into": str(BUNDLE_ID)})
    assert response.status_code == 200
    assert fake.calls == [("import_modrinth", "https://modrinth.com/modpack/aventura", EDITOR, str(BUNDLE_ID))]


def test_neither_a_file_nor_a_reference_is_a_422_before_the_service() -> None:
    client, fake = setup()
    response = client.post("/modpacks/importar", headers=HTMX, data={"ref": "  "})
    assert response.status_code == 422 and fake.calls == []


def test_a_bad_target_pack_is_a_422_before_the_service() -> None:
    client, fake = setup()
    response = client.post("/modpacks/importar", headers=HTMX, data={"ref": "aventura", "into": "../x"})
    assert response.status_code == 422 and fake.calls == []


def test_an_upload_declared_past_the_limit_is_refused_before_reading_it() -> None:
    client, fake = setup()
    response = client.post("/modpacks/importar", data={"ref": "x"},
                           headers={**HTMX, "Content-Length": str(UPLOAD_LIMIT + 2 * 1024 * 1024)})
    assert response.status_code == 413 and "1 GiB" in response.text and fake.calls == []


def test_a_refusal_of_the_service_reaches_the_panel_in_its_words() -> None:
    client, fake = setup()
    fake.error = HTTP_409_CONFLICT("El archivo es para 1.21.1 con fabric; este pack es 1.20.1 con fabric. No se cambió nada.")
    response = client.post("/modpacks/importar", headers=HTMX, data={"ref": "aventura", "into": str(BUNDLE_ID)})
    assert response.status_code == 409 and "No se cambió nada" in response.text



def test_a_failed_import_says_why() -> None:
    client, fake = setup()
    fake.job.status, fake.job.active, fake.job.error = "failed", False, "Modrinth no conoce «x»"
    response = client.get(f"/modpacks/importar/{fake.job.id}", headers=HTMX)
    assert "No se pudo importar: Modrinth no conoce «x»" in " ".join(visible(response.text).split())


def v1_client(fake: FakeImport) -> Any:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.dependences import get_current_actor
    from src.routers.v1 import include_routers_v1

    app = FastAPI()
    include_routers_v1(app)
    app.dependency_overrides[get_current_actor] = lambda: EDITOR
    return TestClient(app, base_url="https://testserver")


def test_v1_imports_a_modrinth_modpack_or_an_mrpack_as_a_job() -> None:
    # U5/S7 (owner, 2026-10-09): /v1 answers 202 with the job, as the panel queues it.
    _, fake = setup()
    client = v1_client(fake)
    by_ref = client.post("/v1/modpack/import/modrinth", params={"ref": "aventura", "into": str(BUNDLE_ID)})
    by_file = client.post("/v1/modpack/import/mrpack", files={"archivo": ("pack.mrpack", b"PK data", "application/zip")})
    assert by_ref.status_code == by_file.status_code == 202, (by_ref.text, by_file.text)
    assert by_ref.json()["kind"] == "modpack_import"
    assert fake.calls == [("import_modrinth", "aventura", EDITOR, str(BUNDLE_ID)),
                          ("import_upload", b"PK data", "pack.mrpack", EDITOR, None)]


def test_v1_refuses_an_mrpack_past_the_limit_before_reading_it() -> None:
    _, fake = setup()
    response = v1_client(fake).post("/v1/modpack/import/mrpack", files={"archivo": ("p.mrpack", b"x", "application/zip")},
                                    headers={"Content-Length": str(UPLOAD_LIMIT + 2 * 1024 * 1024)})
    assert response.status_code == 413 and fake.calls == []


def prism_bytes() -> bytes:
    import zipfile
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("instance.cfg", "name=Aventura\n")
        archive.writestr("mmc-pack.json", "{}")
    return buffer.getvalue()


def test_a_prism_zip_in_the_same_field_is_queued_like_any_upload() -> None:
    # T6 (owner, 2026-10-12): one field takes a .mrpack or a Prism instance, told apart by content;
    # since O6 (owner, 2026-10-21) the job tells them apart, never the request.
    client, fake = setup()
    data = prism_bytes()
    response = client.post("/modpacks/importar", headers=HTMX, files={"archivo": ("Aventura.zip", data, "application/zip")})
    assert response.status_code == 200 and fake.calls == [("import_upload", data, "Aventura.zip", EDITOR, None)]


def test_the_form_and_the_report_speak_of_prism() -> None:
    client, fake = setup()
    page = " ".join(visible(client.get("/modpacks/importar").text).split())
    assert "Un .mrpack, el zip de una instancia de Prism o un modpack publicado en Modrinth: crea un modpack nuevo o reemplaza el contenido de uno." in page
    assert "Archivo .mrpack o zip de Prism" in page
    assert ".mrpack hasta 50 MiB. El zip de Prism hasta 1 GiB: los mundos no se leen; exportarlo sin saves/ sube más rápido." in page
    assert 'accept=".mrpack,.zip"' in client.get("/modpacks/importar").text
    assert "Reemplazar con otro pack" in client.get(f"/modpacks/{BUNDLE_ID}").text
    fake.report.disabled = 3
    fake.job.status, fake.job.active = "done", False
    shown = " ".join(visible(client.get(f"/modpacks/importar/{fake.job.id}", headers=HTMX).text).split())
    assert "Desactivados en Prism: 3 (quedan «Desactivado»)" in shown


def test_an_upload_that_does_not_fit_the_temporary_disk_is_refused_before_reading_it() -> None:
    # Owner (2026-10-12): the uploads in flight may take at most IMPORT_TMP_MAX_MIB of /tmp.
    from src.routers.web import modpacks as web

    client, fake = setup()
    data = prism_bytes()
    web.UPLOADS.in_use = web.UPLOADS.max_bytes - 10  # another upload holds almost all of it
    try:
        response = client.post("/modpacks/importar", headers=HTMX, files={"archivo": ("Aventura.zip", data, "application/zip")})
    finally:
        web.UPLOADS.in_use = 0
    assert response.status_code == 429 and "Hay otra importación subiendo; intenta en un momento" in response.text
    assert fake.calls == []


def test_an_upload_whose_kept_copy_does_not_fit_the_temporary_disk_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    # O6: the file is kept for its job while Starlette's own copy still exists: both must fit.
    from src.library.upload_budget import UploadBudget
    from src.routers.web import modpacks as web

    client, fake = setup()
    data = b"PK" + b"x" * 100_000
    monkeypatch.setattr(web, "UPLOADS", UploadBudget(150_000))  # the request fits; it and its copy do not
    response = client.post("/modpacks/importar", headers=HTMX, files={"archivo": ("pack.mrpack", data, "application/zip")})
    assert response.status_code == 429 and fake.calls == []


def cf_bytes() -> bytes:
    import zipfile
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", "{}")
    return buffer.getvalue()


def test_with_the_key_the_form_takes_a_curseforge_zip_and_a_profile_code() -> None:
    # T5 (owner, 2026-10-12): the same field takes the CurseForge zip; a card takes a profile code.
    client, fake = setup()
    fake.curseforge_enabled = True
    page = client.get("/modpacks/importar").text
    shown = " ".join(visible(page).split())
    assert "Archivo .mrpack, zip de Prism o de CurseForge" in shown
    assert "Código de perfil de CurseForge" in shown and "Importar código" in shown and "Dura 7 días desde que lo compartes." in shown
    data = cf_bytes()
    zipped = client.post("/modpacks/importar", headers=HTMX, files={"archivo": ("pack.zip", data, "application/zip")})
    coded = client.post("/modpacks/importar", headers=HTMX, data={"codigo": " ABCD1234 "})
    assert zipped.status_code == coded.status_code == 200
    assert fake.calls == [("import_upload", data, "pack.zip", EDITOR, None), ("import_curseforge_code", "ABCD1234", EDITOR, None)]


def test_without_the_key_there_is_no_code_and_a_curseforge_zip_is_queued_for_its_job_to_say_why() -> None:
    # O6: the job reads the zip and fails saying to export it as .mrpack (tests/test_import_job_upload.py).
    client, fake = setup()
    shown = " ".join(visible(client.get("/modpacks/importar").text).split())
    assert "Archivo .mrpack o zip de Prism" in shown and "Código de perfil de CurseForge" not in shown
    response = client.post("/modpacks/importar", headers=HTMX, files={"archivo": ("pack.zip", cf_bytes(), "application/zip")})
    assert response.status_code == 200 and [c[0] for c in fake.calls] == ["import_upload"]
    fake.job.status, fake.job.active = "failed", False
    fake.job.error = "Esta instalación no tiene clave de CurseForge: exporta el pack como .mrpack desde Prism."
    failed = " ".join(visible(client.get(f"/modpacks/importar/{fake.job.id}", headers=HTMX).text).split())
    assert "No se pudo importar: Esta instalación no tiene clave de CurseForge" in failed


def test_the_report_lists_what_is_only_on_curseforge_by_its_ids() -> None:
    client, fake = setup()
    fake.report.curseforge_only = [{"project_id": 238222, "file_id": 4567890}]
    fake.job.status, fake.job.active = "done", False
    text = client.get(f"/modpacks/importar/{fake.job.id}", headers=HTMX).text
    shown = " ".join(visible(text).split())
    assert "Solo en CurseForge (1)" in shown and "proyecto 238222, archivo 4567890" in shown
    assert 'href="https://www.curseforge.com/projects/238222"' in text


def test_the_report_says_what_was_pinned_to_the_packs_version_and_what_is_outside_the_tags() -> None:
    client, fake = setup()
    fake.report.pinned = ["P1", "P2", "P3"]
    fake.report.outside = {"P2": "Modrinth no la marca para 1.20.1"}
    fake.report.offered = ["P4"]
    fake.job.status, fake.job.active = "done", False
    shown = " ".join(visible(client.get(f"/modpacks/importar/{fake.job.id}", headers=HTMX).text).split())
    assert "Fijados a la versión del pack: 3 mods" in shown
    assert "1 fuera de las etiquetas de Modrinth: se usan igual y quedan marcados en la lista de mods" in shown
    assert "1 con otra versión elegida o que bajaría: la versión del pack queda ofrecida en su tarjeta" in shown


def test_a_report_without_pins_says_nothing_of_them() -> None:
    client, fake = setup()
    fake.job.status, fake.job.active = "done", False
    shown = visible(client.get(f"/modpacks/importar/{fake.job.id}", headers=HTMX).text)
    assert "versión del pack" not in shown


def test_v1_takes_a_curseforge_zip_and_a_profile_code_as_jobs() -> None:
    _, fake = setup()
    fake.curseforge_enabled = True
    client = v1_client(fake)
    data = cf_bytes()
    zipped = client.post("/v1/modpack/import/mrpack", files={"archivo": ("pack.zip", data, "application/zip")})
    coded = client.post("/v1/modpack/import/curseforge", params={"code": "ABCD1234"})
    assert zipped.status_code == coded.status_code == 202
    assert fake.calls == [("import_upload", data, "pack.zip", EDITOR, None), ("import_curseforge_code", "ABCD1234", EDITOR, None)]


def test_the_import_page_shows_the_import_in_progress_and_then_the_last_report() -> None:
    """The report lived only in the fragment that polls (O9 user-walk, W8, A2): reloading or coming
    back lost it. The page shows the person's import in progress, or the last one's report."""
    client, fake = setup()
    page = client.get("/modpacks/importar").text
    assert "every 2s" not in page and "data-import-report" not in page
    fake.last = True
    page = client.get("/modpacks/importar").text
    result = page[page.index('id="import-result"'):]
    assert f'hx-get="/modpacks/importar/{fake.job.id}"' in result and 'hx-trigger="every 2s"' in result
    fake.job.status, fake.job.active = "done", False
    shown = " ".join(visible(client.get("/modpacks/importar").text).split())
    assert "Importado: 3 mods de Aventura 2.0" in shown


def test_the_import_page_creates_a_pack_or_replaces_the_content_of_one() -> None:
    """O9 D5 (owner, 2026-10-26): one choice for every form on the page, «Crear un pack nuevo» or
    «Reemplazar el contenido de» a pack; the pack's link comes with it chosen."""
    client, _ = setup()
    text = client.get("/modpacks/importar").text
    target = text[text.index('id="import-target"'):]
    target = target[:target.index("</fieldset>")]
    new, replace = (re.search(rf'<input [^>]*value="{v}"[^>]*>', target).group(0) for v in ("nuevo", "reemplazar"))  # type: ignore[union-attr]
    assert 'name="destino"' in new and "checked" in new and "checked" not in replace
    assert f'<option value="{BUNDLE_ID}"' in target and "Crear un pack nuevo" in visible(target)
    forms = re.findall(r'<form [^>]*hx-post="/modpacks/importar"[^>]*>', text)
    assert forms and all('hx-include="#import-target"' in f for f in forms)
    chosen = client.get("/modpacks/importar", params={"en": str(BUNDLE_ID)}).text
    target = chosen[chosen.index('id="import-target"'):]
    assert "checked" in re.search(r'<input [^>]*value="reemplazar"[^>]*>', target).group(0)  # type: ignore[union-attr]
    assert re.search(rf'<option value="{BUNDLE_ID}" selected', target)


def test_a_new_pack_ignores_the_pack_in_the_select_and_replacing_takes_it() -> None:
    client, fake = setup()
    for destino in ("nuevo", "reemplazar"):
        client.post("/modpacks/importar", headers=HTMX, data={"ref": "aventura", "destino": destino, "into": str(BUNDLE_ID)})
    assert [c[-1] for c in fake.calls] == [None, str(BUNDLE_ID)]


def test_the_import_texts_name_what_it_takes_and_both_actions() -> None:
    """O9 A8 (owner's text, 2026-10-26): CurseForge only with its key."""
    client, fake = setup()
    shown = " ".join(visible(client.get("/modpacks/importar").text).split())
    assert ("Un .mrpack, el zip de una instancia de Prism o un modpack publicado en Modrinth: crea un modpack "
            "nuevo o reemplaza el contenido de uno.") in shown
    fake.curseforge_enabled = True
    shown = " ".join(visible(client.get("/modpacks/importar").text).split())
    assert ("Un .mrpack, el zip de una instancia de Prism, un zip o código de CurseForge o un modpack publicado "
            "en Modrinth: crea un modpack nuevo o reemplaza el contenido de uno.") in shown


def test_the_report_links_what_it_left_to_the_version_filter() -> None:
    """O9 D9 (owner, 2026-10-26): the offered and the off-the-tags mods were named only by count."""
    client, fake = setup()
    fake.report.outside = {"P2": "Modrinth no la marca para 1.20.1"}
    fake.report.offered = ["P4"]
    fake.job.status, fake.job.active = "done", False
    text = client.get(f"/modpacks/importar/{fake.job.id}", headers=HTMX).text
    assert f'href="/modpacks/{BUNDLE_ID}?vista=todos&amp;version=ofrecida"' in text
    assert f'href="/modpacks/{BUNDLE_ID}?vista=todos&amp;version=fuera"' in text
