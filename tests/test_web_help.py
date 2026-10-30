"""La ayuda del panel (O3; docs/research/2026-10-21-observaciones-ronda-3.md).

Un «?» junto al título de cada sección de los flujos despliega su explicación en el lugar: un
`<details>` con su `<summary>`, que se abre tocándolo (sirve en el teléfono, sin hover y sin
JavaScript; la CSP no deja scripts ni estilos inline, d-7985ee-491c83). Y una página «Cómo funciona»,
enlazada desde la navegación, que explica cada concepto y el orden de una sesión. Los tests miran el
HTML.
"""
import re

import pytest

from src.web.help import HELP
from tests import test_web_modpacks as modpacks_page
from tests import test_web_servers as servers_page
from tests import test_web_updates as updates_page
from tests._actors import EDITOR
from tests.test_web_base import client as base_client
from tests.test_web_modpacks import BUNDLE_ID, visible

HELP_BLOCK = re.compile(r'<details[^>]*data-help[^>]*>\s*<summary[^>]*aria-label="Ayuda: (?P<topic>[^"]+)"[^>]*>\?</summary>'
                        r'(?P<body>.*?)</details>', re.S)


def helps(text: str) -> dict[str, str]:
    """Each «?» of a page: its topic and the text it opens, as shown."""
    return {m.group("topic"): " ".join(visible(m.group("body")).split()) for m in HELP_BLOCK.finditer(text)}


def test_every_section_of_a_modpack_page_has_its_help() -> None:
    client, _, _ = modpacks_page.setup()
    found = helps(client.get(f"/modpacks/{BUNDLE_ID}").text)
    for topic in ("Mods", "Builds", "Contenido del pack", "Canales", "Loaders secundarios", "Archivos locales"):
        assert topic in found, topic
    assert found["Builds"].startswith(HELP["builds"][0])


def test_the_modpack_list_and_the_updates_page_have_their_help() -> None:
    client, _, _ = modpacks_page.setup()
    assert {"Modpacks", "Nuevo modpack"} <= set(helps(client.get("/modpacks").text))
    client, _, checks = updates_page.setup()
    checks.latest_check = updates_page.check([updates_page.update("A", "Sodium"),
                                             updates_page.update("C", "Lithium", pinned=True)])
    found = helps(client.get(f"/modpacks/{BUNDLE_ID}/actualizaciones").text)
    assert {"Revisión", "Con versión nueva", "Fijadas"} <= set(found)


def test_the_server_pages_have_their_help() -> None:
    client, _ = servers_page.setup()
    assert {"Servidores", "En el panel de Pterodactyl"} <= set(helps(client.get("/servers").text))
    found = helps(client.get(f"/servers/{servers_page.SERVER_ID}").text)
    for topic in ("Modpack", "Instalar", "Último escaneo", "Comparación con el build"):
        assert topic in found, topic


def test_a_help_opens_in_place_and_points_to_how_it_works() -> None:
    client, _, _ = modpacks_page.setup()
    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    block = next(m for m in HELP_BLOCK.finditer(text) if m.group("topic") == "Mods")
    # Closed until touched: no `open`, and no script to open it.
    assert not re.search(r"\sopen(?=[\s>=])", block.group(0).split(">", 1)[0])
    assert f'href="/como-funciona#{HELP["mods"][1]}"' in block.group("body")


def test_every_help_names_an_anchor_of_the_how_it_works_page() -> None:
    text = base_client(EDITOR).get("/como-funciona").text
    for key, (_, anchor) in HELP.items():
        assert f'id="{anchor}"' in text, key


@pytest.mark.parametrize("title", [
    "Qué es un modpack", "Qué es un build", "Qué es resolver", "Qué es fijar una versión", "Los orígenes de una versión fijada",
    "Qué es un puente", "Vincular un servidor", "Qué es escanear", "Qué es instalar", "Qué es un rollback",
    "Una sesión típica"])
def test_how_it_works_explains_each_concept(title: str) -> None:
    response = base_client(EDITOR).get("/como-funciona")
    assert response.status_code == 200
    assert re.search(rf"<h2[^>]*>{re.escape(title)}</h2>", response.text), title


def test_how_it_works_is_in_the_navigation_and_marked_when_open() -> None:
    home = base_client(EDITOR).get("/").text
    assert re.search(r'<a href="/como-funciona"[^>]*>.*?Cómo funciona</a>', home, re.S)
    page = base_client(EDITOR).get("/como-funciona").text
    assert re.search(r'<a href="/como-funciona"\s+aria-current="page"', page)


def test_below_1536_px_the_sections_take_a_row_of_their_own_with_how_it_works_in_it() -> None:
    """Owner, 2026-10-22: at 768 px the sections did not fit beside the name (since batch B). Below
    1536 px they drop to a full-width row, as on a phone; from 1536 px, one row (owner, 2026-10-30: with
    «Conocimiento» the row no longer fit beside the name at 1280 px). «Cómo funciona» is
    one more section, with its text, and nothing else in the header links to it."""
    home = base_client(EDITOR).get("/").text
    nav = re.search(r'<nav aria-label="Secciones" class="([^"]*)".*?</nav>', home, re.S)
    assert nav is not None
    classes = nav.group(1).split()
    assert {"order-last", "w-full", "2xl:order-none", "2xl:w-auto", "2xl:flex-1"} <= set(classes)
    assert not [c for c in classes if c.startswith("md:")]
    assert re.search(r'<li>\s*<a href="/como-funciona"[^>]*>.*?Cómo funciona</a>', nav.group(0), re.S)
    header = home[home.index("<header"):home.index("</header>")]
    assert header.count('href="/como-funciona"') == 1


def test_how_it_works_needs_a_session() -> None:
    response = base_client(None).get("/como-funciona")
    assert response.status_code in (302, 303, 307) and "login" in response.headers["location"]


def test_the_origins_help_names_the_servers_origin() -> None:
    """O9 A6, the owner's accepted text (2026-10-26): «servidor» was missing from both."""
    client, _, _ = modpacks_page.setup()
    shown = " ".join(visible(client.get("/como-funciona").text).split())
    assert ("servidor : la que tiene instalada un servidor vinculado, si difiere de la fijada (una por servidor; "
            "se ve al escanearlo y nunca se pone en uso sola);") in shown  # visible() spaces the <strong>
    assert ("la del pack importado, la elegida a mano, la que tiene instalada cada servidor vinculado o la que "
            "elegiría la resolución") in HELP["origenes"][0]


def test_how_it_works_says_what_a_test_build_is() -> None:
    """O8 B1, the owner's accepted text (2026-10-26, «claros y cortos», then «un poco más largo y claro»)."""
    client, _, _ = modpacks_page.setup()
    shown = " ".join(visible(client.get("/como-funciona").text).split())
    assert 'Una prueba es un build para probar un cambio sin comprometer el pack.' in shown
    assert 'Cambia el pack (fija o agrega un mod) y pulsa «Construir como prueba».' in shown
    assert 'Instálala en un servidor de pruebas. Mientras no la apruebes, no es el último build ni sube el piso de versiones.' in shown
    assert 'Si funcionó, pulsa «Aprobar». Si falló, deshaz el cambio en el pack: la prueba queda marcada y no cuenta.' in shown


def test_how_it_works_says_what_the_knowledge_base_is() -> None:
    """K-Q5 of d-7985ee-57d185: the owner's text, as accepted."""
    text = base_client(EDITOR).get("/como-funciona").text
    for sentence in ("La base de conocimiento guarda las combinaciones que fallan: dos mods que no funcionan juntos, o una versión que falla sola. Vale para todos los packs.",
                     "Al comparar un build que funcionó con uno que falló, pulsa «Marcar como sospechoso» junto al mod que crees culpable.",
                     "Un operador la confirma o la descarta. Mientras sea sospecha, solo se ve aquí y al comparar.",
                     "Confirmada, avisa en cualquier pack que tenga esa combinación, también al actualizar. Solo avisa: nunca cambia ni bloquea nada.",
                     "Cuando una versión nueva lo arregla, anótala en «Arreglado en»: deja de avisar a quien ya la tiene."):
        assert sentence in text
    assert 'href="#conocimiento"' in text  # the page's index links it, as every section
    assert 'id="conocimiento"' in text and text.index('id="build"') < text.index('id="conocimiento"') < text.index('id="puente"')


def test_the_owner_s_help_texts_of_2026_10_30() -> None:
    """d-7985ee-c0cbfb: the pack resolves itself (no «resolver»), and the knowledge base and a build's notes
    have their «?»."""
    assert HELP["modpacks"][0] == ("Cada modpack es una lista de mods para una versión de Minecraft y un loader. "
                                   "Ábrelo para agregar mods y crear builds: se resuelve solo.")
    assert HELP["con-version-nueva"][0] == "Mods con una versión más nueva para este pack. Para usarla, crea un build nuevo."
    assert HELP["conocimiento"] == ("Las combinaciones de mods que fallan, en todos los packs. Se marcan al comparar dos "
                                    "builds y un operador las confirma. Solo avisan: nunca cambian ni bloquean nada.",
                                    "conocimiento")
    assert HELP["notas-build"] == ("Anota si este build funcionó, falló o tuvo problemas, con lo que viste y el error si "
                                   "lo hay. Las notas ayudan a comparar builds y a marcar sospechas en «Conocimiento».",
                                   "build")


def test_the_knowledge_page_and_a_build_s_notes_have_their_help() -> None:
    from tests.test_web_knowledge import base_setup
    client, _ = base_setup([])
    assert "Conocimiento" in helps(client.get("/conocimiento").text)
    page = modpacks_page.setup_all()[0].get(f"/modpacks/{BUNDLE_ID}/builds/2").text
    assert "¿Funcionó?" in helps(page)


def test_an_open_help_keeps_its_question_mark_beside_the_title() -> None:
    """Owner, 2026-10-30 (d-7985ee-c0cbfb): the <details> lays its parts in the title's row (display: contents),
    the explanation takes a row of its own below; opening it no longer moves the «?» (the sweep measures it)."""
    text = modpacks_page.setup_all()[0].get(f"/modpacks/{BUNDLE_ID}").text
    block = re.search(r'<details class="([^"]*)" data-help>.*?<p class="([^"]*)"', text, re.S)
    assert block is not None
    assert "contents" in block.group(1).split() and "open:basis-full" not in block.group(1)
    assert "basis-full" in block.group(2).split()

