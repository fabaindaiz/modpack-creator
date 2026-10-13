"""The panel's style (batch B, B3; d-7985ee-8763b7, d-7985ee-01ad81, d-7985ee-8762fc, d-7985ee-2d88d9):
the owner's palette as Tailwind tokens, Inter served from the panel with its license, Lucide icons
from a sprite built from the list of the icons used, and a footer that says the panel is not
Modrinth's. The look itself is the visual sweep's and the owner's."""
import re
from pathlib import Path

import pytest

from src import web
from tests._actors import EDITOR
from tests.test_web_base import client

ROOT = Path(__file__).resolve().parent.parent
APP_CSS = ROOT / "src/web/assets/app.css"
ICONS = ROOT / "src/web/assets/icons.txt"
TEMPLATES = ROOT / "src/web/templates"
LUCIDE = ROOT / "node_modules/lucide-static/icons"


def css() -> str:
    return APP_CSS.read_text()


@pytest.mark.parametrize("token,value", [
    ("--color-zinc-950", "#111316"), ("--color-zinc-900", "#1a1d21"), ("--color-zinc-800", "#2a2e33"),
    ("--color-zinc-200", "#e3e6ea"), ("--color-zinc-50", "#f6f7f8"),
    ("--color-emerald-400", "#22c55e"), ("--color-emerald-600", "#15803d"),
])
def test_the_theme_redefines_the_palette_with_the_owner_s_colors(token: str, value: str) -> None:
    theme = css()[css().index("@theme"):]
    assert re.search(rf"{re.escape(token)}:\s*{value};", theme), token


def test_the_theme_adds_semantic_tokens_for_light_and_dark() -> None:
    text = css()
    for token in ("--color-surface", "--color-raised", "--color-line", "--color-muted", "--color-accent"):
        assert token in text, token
    assert "prefers-color-scheme: dark" in text


def test_inter_is_served_by_the_panel_with_its_license() -> None:
    font = web.STATIC_DIR / "fonts/InterVariable.woff2"
    assert font.read_bytes()[:4] == b"wOF2"
    assert "SIL OPEN FONT LICENSE" in (web.STATIC_DIR / "fonts/LICENSE-Inter.txt").read_text().upper()
    text = css()
    assert re.search(r'@font-face\s*{[^}]*font-family:\s*"Inter"[^}]*url\("/static/fonts/InterVariable\.woff2"\)', text)
    assert re.search(r'--font-sans:\s*"Inter"', text)


def used_icons() -> set[str]:
    found: set[str] = set()
    for template in TEMPLATES.rglob("*.html"):
        found |= set(re.findall(r'(?:ui\.icon\(|icon_name=)"([a-z0-9-]+)"', template.read_text()))
    return found


def test_every_icon_a_template_uses_is_in_the_list() -> None:
    listed = set(ICONS.read_text().split())
    assert used_icons(), "no template uses an icon"
    assert used_icons() <= listed, sorted(used_icons() - listed)


def test_every_listed_icon_exists_in_lucide() -> None:
    if not LUCIDE.is_dir():
        pytest.skip("node_modules not installed (npm ci)")
    missing = [name for name in ICONS.read_text().split() if not (LUCIDE / f"{name}.svg").is_file()]
    assert missing == []


def test_the_built_sprite_has_a_symbol_for_every_listed_icon() -> None:
    sprite = web.DIST_DIR / "icons.svg"
    if not sprite.is_file():
        pytest.skip("assets not built (npm run build)")
    text = sprite.read_text()
    for name in ICONS.read_text().split():
        assert f'<symbol id="{name}"' in text, name
    assert "ISC" in (web.DIST_DIR / "LICENSE-lucide.txt").read_text()


def test_an_icon_is_a_use_of_the_sprite_without_inline_style() -> None:
    macro = web.templates.env.from_string('{% import "_macros.html" as ui %}{{ ui.icon("server", "size-5") }}')
    html = macro.render()
    assert '<svg' in html and 'aria-hidden="true"' in html and "size-5" in html
    assert re.search(r'<use href="/static/dist/icons\.svg(\?v=[0-9a-f]+)?#server"', html)
    assert "style=" not in html


def test_the_footer_says_the_panel_is_not_modrinth_s_and_links_the_licenses() -> None:
    text = client(EDITOR).get("/").text
    assert "Modpack Creator no está afiliado a Modrinth." in text
    assert 'href="/static/LICENSES.html"' in text
    licenses = (web.STATIC_DIR / "LICENSES.html").read_text()
    for named in ("Inter", "Lucide", "htmx", "/static/fonts/LICENSE-Inter.txt", "/static/dist/LICENSE-lucide.txt",
                  "/static/dist/LICENSE-htmx.txt"):
        assert named in licenses, named


def test_controls_take_the_owner_s_radii() -> None:
    macros = (TEMPLATES / "_macros.html").read_text()
    button = macros[macros.index("{% macro button("):macros.index("{% endmacro %}", macros.index("{% macro button("))]
    badge = macros[macros.index("{% macro badge("):macros.index("{% endmacro %}", macros.index("{% macro badge("))]
    assert "rounded-lg" in button and "rounded-md" not in button  # 8px
    assert "rounded-full" in badge  # a pill
    assert "rounded-xl" in macros[macros.index("{% macro card_list("):]  # 12px cards
