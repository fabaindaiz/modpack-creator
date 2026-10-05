"""La ficha de un mod en el panel (spec §2): la página `/mods/{id}`, el panel compacto y la tabla
con todas las versiones. `ModpackService` se sustituye con un doble en el contenedor, como en
`tests/test_web_servers.py`; el doble anota qué le pidieron."""
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

import pytest
from beanie import PydanticObjectId
from dependency_injector import providers
from fastapi.testclient import TestClient

from src.exceptions import HTTP_404_NOT_FOUND, UpstreamError
from src.model.modrinth import ModrinthProject
from src.model.modrinth.project import ModrinthEnvironment, ModrinthProjectType
from src.model.users import Actor
from src.services.modpack import ModPage, ModUse, ServerUse, VersionRow
from tests._actors import EDITOR, session_for
from tests._session import session_cookie

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
BUNDLE_A, BUNDLE_B, SERVER = PydanticObjectId(), PydanticObjectId(), PydanticObjectId()
HTMX = {"HX-Request": "true", "Sec-Fetch-Site": "same-origin"}
EVIL = '<script>alert(1)</script><img src=x onerror=alert(2)>'


def modrinth_project(title: str = "Sodium", **changes: Any) -> ModrinthProject:
    fields: dict[str, Any] = dict(
        project_id="AANobbMI", slug="sodium", team="t", title=title, description="Rendimiento sin cambios visuales",
        body="", environment=[ModrinthEnvironment.client_only_server_optional], project_type=ModrinthProjectType.mod,
        downloads=1234, followers=1, categories=["optimization"], additional_categories=[], versions=[],
        game_versions=["1.20.1", "1.21.1"], loaders=["fabric", "quilt"], issues_url="https://github.com/x/issues",
        source_url="javascript:alert(1)", wiki_url=None, icon_url="https://cdn.modrinth.com/data/AANobbMI/icon.png")
    return ModrinthProject.model_construct(**{**fields, **changes})


def version_row(number: str, day: int, channel: str = "release") -> VersionRow:
    return VersionRow(version_id=f"v-{number}", version_number=number, channel=channel,
                      published=datetime(2026, 9, day, tzinfo=timezone.utc), game_versions=["1.21.1"],
                      loaders=["fabric"])


def mod_page(title: str = "Sodium", **changes: Any) -> ModPage:
    fields: dict[str, Any] = dict(
        project=modrinth_project(title),
        uses=[ModUse(bundle_id=str(BUNDLE_A), bundle_name="Pack A", origin="admin", dependency_of=[],
                     recommended=(5, "0.8.13"), latest=(7, "0.8.14")),
              ModUse(bundle_id=str(BUNDLE_B), bundle_name="Pack B", origin=None, dependency_of=["Iris"],
                     recommended=None, latest=None)],
        servers=[ServerUse(server_id=str(SERVER), server_name="Survival", version="0.8.13", scanned_at=T0)],
        versions=[version_row("0.8.14", 20), version_row("0.8.13", 10, "beta")], versions_partial=True,
        titles={"AANobbMI": title, "P7dR8mSH": "Fabric API", "PREV": "Lithium"}, dependencies=["P7dR8mSH"])
    return ModPage(**{**fields, **changes})


class FakeModpack:
    def __init__(self) -> None:
        self.page = mod_page()
        self.all_versions = [version_row("0.9.0", 25), version_row("0.8.14", 20), version_row("0.1.0", 1)]
        self.error: Optional[Exception] = None
        self.calls: list[tuple[Any, ...]] = []

    async def mod_page(self, project_id: str, named: Sequence[str] = ()) -> ModPage:
        self.calls.append(("mod_page", project_id, list(named)))
        if self.error is not None:
            raise self.error
        return self.page

    async def mod_versions_all(self, project_id: str) -> list[VersionRow]:
        self.calls.append(("mod_versions_all", project_id))
        if self.error is not None:
            raise self.error
        return self.all_versions


@pytest.fixture(autouse=True)
def reset_overrides() -> Any:
    yield
    import main

    main.container.modpackService.reset_override()


def setup(actor: Optional[Actor] = EDITOR) -> tuple[TestClient, FakeModpack]:
    import main

    fake = FakeModpack()
    main.container.modpackService.override(providers.Object(fake))
    client = TestClient(main.app, base_url="https://testserver", follow_redirects=False)
    if actor is not None:
        client.cookies.set("session", session_cookie(session_for(actor)))
    return client, fake


def test_page_shows_identity_uses_and_partial_versions() -> None:
    client, fake = setup()
    response = client.get("/mods/AANobbMI")
    assert response.status_code == 200
    text = response.text
    assert fake.calls == [("mod_page", "AANobbMI", [])]
    # Identity.
    assert "Sodium" in text and "Rendimiento sin cambios visuales" in text and "optimization" in text
    assert 'src="https://cdn.modrinth.com/data/AANobbMI/icon.png"' in text
    assert "cliente" in text and "fabric, quilt" in text and "1.21.1" in text
    assert 'href="https://modrinth.com/project/AANobbMI"' in text and 'href="https://github.com/x/issues"' in text
    assert "javascript:" not in text  # a source link that is not https is left out
    assert "Fabric API" in text  # its dependencies, by title
    # Where it is used, with links.
    assert f'href="/modpacks/{BUNDLE_A}"' in text and "Pack A" in text and f'href="/modpacks/{BUNDLE_B}"' in text
    assert "dependencia de Iris" in text
    assert "#5 recomendado, 0.8.13" in text and "#7 último, 0.8.14" in text
    assert f'href="/servers/{SERVER}"' in text and "Survival" in text
    # Versions from the catalog, newest first, said to be partial, and the button to load them all.
    table = text[text.index('id="mod-versions"'):]
    assert table.index("0.8.14") < table.index("0.8.13")
    assert "parcial" in table and "Cargar todas desde Modrinth" in table
    assert 'hx-get="/mods/AANobbMI/versiones"' in text and 'hx-target="#mod-versions"' in text
    assert 'hx-swap="outerHTML"' in text


def test_page_without_uses_says_so() -> None:
    client, fake = setup()
    fake.page = mod_page(uses=[], servers=[], versions=[], dependencies=[])
    text = client.get("/mods/AANobbMI").text
    assert "Ningún modpack lo usa" in text and "Ningún servidor" in text and "El catálogo no tiene versiones" in text


def test_panel_is_compact_with_a_link_to_the_full_page() -> None:
    client, fake = setup()
    response = client.get("/mods/AANobbMI/panel", headers=HTMX)
    assert response.status_code == 200
    text = response.text
    assert "<html" not in text and "Sodium" in text and "Pack A" in text and "Survival" in text
    assert 'href="/mods/AANobbMI"' in text and "Abrir ficha completa" in text
    assert 'id="mod-versions"' not in text and "Cargar todas" not in text
    assert "← " not in text  # no way back without `desde`


def test_panel_with_desde_offers_the_way_back() -> None:
    client, fake = setup()
    text = client.get("/mods/AANobbMI/panel?desde=PREV", headers=HTMX).text
    assert fake.calls == [("mod_page", "AANobbMI", ["PREV"])]  # its title comes from the same single fill
    assert "← Lithium" in text
    assert 'hx-get="/mods/PREV/panel"' in text and 'hx-target="#mod-drawer-body"' in text


def test_panel_ignores_an_invalid_desde() -> None:
    client, fake = setup()
    response = client.get("/mods/AANobbMI/panel?desde=a%20b%22%3E", headers=HTMX)
    assert response.status_code == 200
    assert fake.calls == [("mod_page", "AANobbMI", [])] and "← " not in response.text


@pytest.mark.parametrize("path", ["/mods/a%20b", "/mods/a%20b/panel", "/mods/a%20b/versiones", "/mods/" + "x" * 201])
def test_an_invalid_id_is_a_404(path: str) -> None:
    client, fake = setup()
    response = client.get(path)
    assert response.status_code == 404 and fake.calls == []


def test_a_mod_modrinth_does_not_know_is_a_404_in_spanish() -> None:
    client, fake = setup()
    fake.error = HTTP_404_NOT_FOUND("Modrinth no conoce el mod NOPE")
    response = client.get("/mods/NOPE")
    assert response.status_code == 404 and "Modrinth no conoce el mod NOPE" in response.text


@pytest.mark.parametrize("error", [UpstreamError("Modrinth", 502, raw="Bad gateway"), TimeoutError()])
@pytest.mark.parametrize("path,htmx", [("/mods/AANobbMI", False), ("/mods/AANobbMI/panel", True),
                                       ("/mods/AANobbMI/versiones", True)])
def test_modrinth_down_is_a_notice_with_retry_never_a_500(error: Exception, path: str, htmx: bool) -> None:
    client, fake = setup()
    fake.error = error
    response = client.get(path, headers=HTMX if htmx else {})
    assert response.status_code < 500 or response.status_code in (502, 504)
    text = response.text
    assert "Modrinth no respondió" in text and "Reintentar" in text
    assert f'hx-get="{path}"' in text or f'href="{path}"' in text


def test_versiones_replaces_the_table_with_every_version() -> None:
    client, fake = setup()
    response = client.get("/mods/AANobbMI/versiones", headers=HTMX)
    assert response.status_code == 200
    text = response.text
    assert fake.calls == [("mod_versions_all", "AANobbMI")]
    assert text.lstrip().startswith('<div id="mod-versions"')
    assert "0.9.0" in text and "0.1.0" in text and "parcial" not in text and "Cargar todas" not in text
    assert "3 versiones" in text


def test_strings_from_modrinth_are_escaped() -> None:
    client, fake = setup()
    fake.page = mod_page(EVIL, uses=[ModUse(bundle_id=str(BUNDLE_A), bundle_name=EVIL, origin=None, dependency_of=[EVIL],
                                            recommended=(1, EVIL), latest=None)],
                         servers=[ServerUse(server_id=str(SERVER), server_name=EVIL, version=EVIL, scanned_at=T0)],
                         versions=[VersionRow(version_id="v", version_number=EVIL, channel="release", published=T0,
                                              game_versions=[EVIL], loaders=[EVIL])],
                         titles={"P7dR8mSH": EVIL, "PREV": EVIL}, dependencies=["P7dR8mSH"])
    fake.page.project.description = EVIL
    fake.page.project.categories = [EVIL]
    for response in (client.get("/mods/AANobbMI"), client.get("/mods/AANobbMI/panel?desde=PREV", headers=HTMX)):
        assert "<script>alert" not in response.text and "<img src=x" not in response.text
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text


def test_an_icon_off_the_cdn_is_not_loaded() -> None:
    client, fake = setup()
    fake.page = mod_page()
    fake.page.project.icon_url = "https://evil.example/icon.png"
    text = client.get("/mods/AANobbMI").text
    assert "evil.example" not in text


def test_without_a_session_the_page_goes_to_login() -> None:
    client, _ = setup(actor=None)
    response = client.get("/mods/AANobbMI")
    assert response.status_code == 303 and response.headers["location"].startswith("/v1/login")
