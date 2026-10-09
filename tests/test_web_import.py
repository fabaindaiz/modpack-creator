"""Importar un modpack desde el panel (spec 2026-10-09-importar-modpacks): la página, el formulario
en el detalle de un pack, la subida con su tope y el informe. El servicio es un doble."""
from typing import Any, Optional

import pytest
from dependency_injector import providers

from datetime import datetime, timezone

from beanie import PydanticObjectId

from src.exceptions import HTTP_409_CONFLICT
from src.model.jobs import Job
from src.model.users import Actor
from src.services.modpack_import import ImportReport
from tests._actors import EDITOR
from tests.test_web_modpacks import BUNDLE_ID, HTMX, setup_all, visible

UPLOAD_LIMIT = 50 * 1024 * 1024


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

    async def import_mrpack(self, data: bytes, actor: Actor, into: Optional[str] = None) -> Job:
        self.calls.append(("import_mrpack", data, actor, into))
        if self.error:
            raise self.error
        return self.job

    async def import_modrinth(self, ref: str, actor: Actor, into: Optional[str] = None) -> Job:
        self.calls.append(("import_modrinth", ref, actor, into))
        if self.error:
            raise self.error
        return self.job

    async def import_status(self, job_id: str) -> tuple[Job, Optional[ImportReport]]:
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
    assert 'type="file"' in text and 'name="archivo"' in text and 'accept=".mrpack"' in text
    assert 'name="ref"' in text and 'name="into"' not in text


def test_the_pack_page_offers_replacing_its_content_and_says_what_stays() -> None:
    client, _ = setup()
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    section = text[text.index('id="import-section"'):]
    assert f'name="into" value="{BUNDLE_ID}"' in section and 'name="archivo"' in section and 'name="ref"' in section
    assert "lo que agregaste a mano se queda" in visible(section)


def test_an_uploaded_mrpack_queues_its_import_and_shows_it_until_the_report() -> None:
    client, fake = setup()
    response = client.post("/modpacks/importar", headers=HTMX, files={"archivo": ("pack.mrpack", b"PK data", "application/zip")})
    assert response.status_code == 200 and response.headers["HX-Trigger"] == "job-started"
    assert fake.calls == [("import_mrpack", b"PK data", EDITOR, None)]
    assert "Importación de modpack" in response.text and 'hx-trigger="every 2s"' in response.text
    assert f'hx-get="/modpacks/importar/{fake.job.id}"' in response.text
    fake.job.status, fake.job.active = "done", False
    response = client.get(f"/modpacks/importar/{fake.job.id}", headers=HTMX)
    assert "every 2s" not in response.text
    shown = " ".join(visible(response.text).split())
    assert "Importado: 3 mods de Aventura 2.0" in shown
    assert "No están en Modrinth (2)" in shown and "mods/privado.jar" in shown and "overrides/mods/propio.jar" in shown
    assert "Súbelos al servidor a mano; al importar el servidor quedan como archivos locales." in shown
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
    assert response.status_code == 413 and "50 MiB" in response.text and fake.calls == []


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
