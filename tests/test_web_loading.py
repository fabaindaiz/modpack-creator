"""Lo que sigue esperando a Modrinth dentro de la petición dice que carga (O6, dueño 2026-10-21): las
actualizaciones de un pack, la comparación de dos builds y la vista previa de una actualización
tardan hasta ~15 s. Sin script ni style inline (CSP, d-7985ee-491c83): un atributo que lee app.js, o
el indicador de htmx con su regla en app.css. Cómo se ve lo mira el barrido visual."""
import re
from pathlib import Path

from tests._actors import EDITOR, OPERATOR
from tests.test_web_home import calm_fakes, strip
from tests.test_web_base import BUNDLE_ID, FakeChecks, T0, client as home_client
from tests.test_web_modpacks import setup as modpacks_setup
from tests.test_web_servers import SERVER_ID, setup as servers_setup

ROOT = Path(__file__).resolve().parent.parent


def tag_with(text: str, needle: str) -> str:
    """The opening tag that holds `needle`."""
    found = re.search(r"<(a|button|select|p)\b[^>]*" + re.escape(needle) + r"[^>]*>", text)
    assert found, f"no tag with {needle!r}"
    return found.group(0)


def test_the_pack_s_updates_link_says_it_is_loading_once_clicked() -> None:
    client, _, _ = modpacks_setup()
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    link = tag_with(text, f'href="/modpacks/{BUNDLE_ID}/actualizaciones"')
    assert 'data-loading="Cargando actualizaciones…"' in link


def test_comparing_two_builds_says_it_is_loading_once_sent() -> None:
    client, _, _ = modpacks_setup()
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    form = text[text.index(f'action="/modpacks/{BUNDLE_ID}/builds/compare"'):]
    form = form[:form.index("</form>")]
    assert 'data-loading="Comparando…"' in tag_with(form, 'type="submit"')


def test_the_dashboard_s_links_to_the_updates_say_they_are_loading() -> None:
    from src.model.modpack.update_check import AvailableUpdate, UpdateCheck

    check = UpdateCheck.model_construct(bundle_id=BUNDLE_ID, build_number=3, checked_at=T0, error=None, updates=[
        AvailableUpdate(project_id="a", from_version="1", to_version="2")])
    text = home_client(EDITOR, calm_fakes(), FakeChecks({BUNDLE_ID: check})).get("/").text
    link = tag_with(strip(text), f'href="/modpacks/{BUNDLE_ID}/actualizaciones"')
    assert 'data-loading="Cargando…"' in link
    other = [m.group(0) for m in re.finditer(rf'<a [^>]*href="/modpacks/{BUNDLE_ID}/actualizaciones"[^>]*>', text)]
    assert other and all("data-loading=" in tag for tag in other)


def test_another_build_s_preview_shows_that_it_loads() -> None:
    client, _ = servers_setup(OPERATOR)
    text = client.get(f"/servers/{SERVER_ID}").text
    select = tag_with(text, f'hx-get="/servers/{SERVER_ID}/update/preview" hx-trigger="change"')
    assert 'hx-indicator="#update-preview-loading"' in select
    indicator = tag_with(text, 'id="update-preview-loading"')
    assert 'class="htmx-indicator' in indicator and "style=" not in indicator
    after = text[text.index('id="update-preview-loading"'):]
    assert after[after.index(">") + 1:].startswith("Cargando cambios…")


def test_the_indicator_is_hidden_until_its_request_runs() -> None:
    css = (ROOT / "src/web/assets/app.css").read_text()
    assert re.search(r"\.htmx-indicator\s*\{\s*display:\s*none;?\s*\}", css)
    assert ".htmx-request.htmx-indicator" in css


def test_app_js_says_a_slow_page_is_loading_and_puts_it_back_when_the_browser_returns() -> None:
    js = (ROOT / "src/web/static/app.js").read_text()
    assert "a[data-loading]" in js and "button[data-loading]" in js and "aria-busy" in js
    # Back from the slow page, a page from the browser's cache shows the link as it was.
    assert "pageshow" in js and "persisted" in js


def test_a_server_jobs_result_is_brought_into_view_when_it_arrives() -> None:
    """«Escanear ahora», «Simular» and «Importar» answer at the top of the page, far from the button
    (O9 user-walk, A13): the place says so and app.js scrolls to it on that swap."""
    page = (ROOT / "src/web/templates/servers/detail.html").read_text()
    assert re.search(r'id="server-job"[^>]*data-reveal', page)
    js = (ROOT / "src/web/static/app.js").read_text()
    assert "[data-reveal]" in js and "scrollIntoView" in js
