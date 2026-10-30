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
    assert re.search(r'@font-face\s*{[^}]*font-family:\s*"Inter"[^}]*url\("/static/fonts/InterVariable\.woff2\?v=[0-9a-f]+"\)', text)
    assert re.search(r'--font-sans:\s*"Inter"', text)


def test_the_font_url_changes_with_the_font() -> None:
    """Review of batch B, minor 7: the stylesheet's URL carries its hash, the font's did not, so a
    browser could keep an old font after it changed. The version is the font's own hash: a new font
    without a new version fails here."""
    import hashlib

    digest = hashlib.sha256((web.STATIC_DIR / "fonts/InterVariable.woff2").read_bytes()).hexdigest()[:12]
    assert f'url("/static/fonts/InterVariable.woff2?v={digest}")' in css()


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


# The icons lucide-static 1.56.0's LICENSE says derive from Feather (MIT, Cole Bemis), under the names
# the panel uses: Lucide renamed alert-triangle to triangle-alert and help-circle to circle-help (the
# old names are aliases of the same drawing in that package).
FEATHER = {"airplay", "alert-circle", "alert-octagon", "triangle-alert", "aperture", "arrow-down", "arrow-left",
           "arrow-right", "arrow-up", "at-sign", "calendar", "cast", "check", "chevron-down", "chevron-left",
           "chevron-right", "chevron-up", "circle", "clipboard", "clock", "code", "command", "compass",
           "crosshair", "database", "download", "external-link", "feather", "hash", "headphones", "circle-help",
           "info", "key", "layout", "link", "loader", "lock", "log-in", "log-out", "maximize", "minimize", "minus",
           "monitor", "moon", "move", "music", "plus", "power", "radio", "rss", "search", "server", "share",
           "sidebar", "smartphone", "square", "tablet", "target", "terminal", "trash", "trash-2", "triangle", "tv",
           "type", "upload", "x", "zoom-in", "zoom-out"}


def test_the_licenses_name_feather_for_the_icons_that_come_from_it_and_tailwind() -> None:
    """Review of batch B, minor 1: some of the served icons are Feather's (MIT) in Lucide's package, and
    the stylesheet is Tailwind's output (MIT): both are named, with their notice."""
    licenses = (web.STATIC_DIR / "LICENSES.html").read_text()
    from_feather = sorted(set(ICONS.read_text().split()) & FEATHER)
    assert from_feather, "no listed icon comes from Feather"
    assert "Feather" in licenses and "Cole Bemis" in licenses
    for name in from_feather:
        assert f"<code>{name}</code>" in licenses, name
    assert "Tailwind CSS" in licenses and "Tailwind Labs, Inc." in licenses
    assert licenses.count("Permission is hereby granted, free of charge") == 1  # Tailwind's MIT text, whole
    assert "Lucide" in licenses and "ISC" in licenses


def test_the_feather_list_is_the_one_lucide_ships() -> None:
    notice = LUCIDE.parent / "LICENSE"
    if not notice.is_file():
        pytest.skip("node_modules not installed (npm ci)")
    text = notice.read_text()
    listed = set(text[text.index("derived from the Feather project:"):text.index("The MIT License")].split(":")[1]
                 .replace(",", " ").split())
    renamed = {"alert-triangle": "triangle-alert", "help-circle": "circle-help"}
    used = set(ICONS.read_text().split())
    assert {renamed.get(n, n) for n in listed} & used == FEATHER & used


def test_controls_take_the_owner_s_radii() -> None:
    macros = (TEMPLATES / "_macros.html").read_text()
    button = macros[macros.index("{% macro button("):macros.index("{% endmacro %}", macros.index("{% macro button("))]
    badge = macros[macros.index("{% macro badge("):macros.index("{% endmacro %}", macros.index("{% macro badge("))]
    assert "rounded-lg" in button and "rounded-md" not in button  # 8px
    assert "rounded-full" in badge  # a pill
    assert "rounded-xl" in macros[macros.index("{% macro card_list("):]  # 12px cards


def contrast(a: str, b: str) -> float:
    """WCAG 2.x contrast ratio of two #rrggbb colours."""
    def luminance(hex_colour: str) -> float:
        channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, bl = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * r + 0.7152 * g + 0.0722 * bl
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def colour(token: str) -> str:
    found = re.search(rf"{re.escape(token)}:\s*(#[0-9a-f]{{6}});", css())
    assert found is not None, token
    return found.group(1)


@pytest.mark.parametrize("background", ["--color-zinc-50", "--color-zinc-100"])
def test_light_muted_text_reads_on_the_light_surfaces(background: str) -> None:
    """Final review of batch B: zinc-500 (dates, headers, step notes in light mode) was 4.0:1 on the
    page; WCAG AA asks 4.5:1 for small text, also on white cards."""
    for muted in ("--color-zinc-500", "--color-zinc-600"):
        assert contrast(colour(muted), colour(background)) >= 4.5, (muted, background)
    assert contrast(colour("--color-zinc-500"), "#ffffff") >= 4.5


# The semantic tokens (owner, 2026-10-21, «Migrar a los tokens»; d-7985ee-cc655d): every template
# and the Python that writes classes name a role (text-ink, bg-raised, border-line, text-danger...),
# never a raw scale step, so a colour changes in app.css once, for light and dark together.
RAW_COLOUR = re.compile(
    r"(?<![\w-])(?:[\w\[\]&>='().:-]*:)?"
    r"(?:bg|text|border|ring|divide|outline|accent|fill|stroke|from|via|to|decoration|placeholder|shadow)"
    r"-(?:white|black|(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue"
    r"|indigo|violet|purple|fuchsia|pink|rose)-\d{2,3})(?:/\d+)?(?![\w-])")
# Raw colours that stay on purpose, each with why no token fits: (file relative to src/web, class).
RAW_COLOUR_KEPT: dict[tuple[str, str], str] = {}
SOURCES = sorted([*TEMPLATES.rglob("*.html"), *(ROOT / "src/web").glob("*.py")])


def test_templates_name_semantic_tokens_not_raw_colours() -> None:
    found = []
    for source in SOURCES:
        for number, line in enumerate(source.read_text().splitlines(), 1):
            for raw in RAW_COLOUR.findall(line):
                if (str(source.relative_to(ROOT / "src/web")), raw) not in RAW_COLOUR_KEPT:
                    found.append(f"{source.relative_to(ROOT)}:{number}: {raw}")
    assert found == [], "\n".join(found[:40]) + f"\n... {len(found)} in all"


def test_the_raw_colour_check_sees_a_raw_class() -> None:
    for raw in ("text-zinc-900", "dark:text-zinc-100", "hover:bg-emerald-700", "dark:bg-red-950/60", "bg-white",
                "max-sm:[&>tr>td]:before:text-zinc-500", "focus-visible:outline-emerald-600"):
        assert RAW_COLOUR.findall(f'class="px-2 {raw} py-1"') == [raw], raw
    for token in ("text-ink", "bg-raised", "border-line", "text-danger", "bg-danger-soft", "ring-control"):
        assert RAW_COLOUR.findall(f'class="px-2 {token}"') == [], token


def scheme_block(dark: bool) -> dict[str, str]:
    text = css()
    start = text.index("@media (prefers-color-scheme: dark)") if dark else text.index(":root {")
    body = text[text.index("{", text.index(":root", start)) + 1:]
    body = body[:body.index("}")]
    return dict(re.findall(r"--([\w-]+):\s*([^;]+);", body))


def test_every_semantic_token_has_a_light_and_a_dark_value_and_a_class() -> None:
    light, dark = scheme_block(False), scheme_block(True)
    assert set(light) == set(dark), set(light) ^ set(dark)
    inline = css()[css().index("@theme inline"):]
    for name in light:
        assert re.search(rf"--color-{re.escape(name)}:\s*var\(--{re.escape(name)}\);", inline), name


def test_body_text_takes_the_palette_s_text_colours() -> None:
    """Review of batch B, minor 6: the body was zinc-900/100, not the palette's #16181c / #e6e8eb."""
    assert scheme_block(False)["ink"] == "#16181c"
    assert scheme_block(True)["ink"] == "#e6e8eb"
    base = (TEMPLATES / "base.html").read_text()
    body = re.search(r'<body class="([^"]+)"', base)
    assert body is not None and {"bg-surface", "text-ink"} <= set(body.group(1).split())


@pytest.mark.parametrize("dark", [False, True])
def test_the_text_tokens_read_on_the_surfaces(dark: bool) -> None:
    tokens = scheme_block(dark)
    for text in ("ink", "ink-soft", "muted"):
        for background in ("surface", "raised"):
            assert contrast(tokens[text], tokens[background]) >= 4.5, (text, background, dark)


def test_notices_and_alerts_take_the_panel_s_corners() -> None:
    """Review of batch B, minor 6: ui.notice and an alert kept rounded-md (6px) while the panel's
    boxes are rounder; a tinted box (a notice's tones or a -soft background) is never rounded-md."""
    found = []
    for source in TEMPLATES.rglob("*.html"):
        for classes in re.findall(r'class="([^"]*)"', source.read_text()):
            tinted = "NOTICE_TONES" in classes or re.search(r"\bbg-[\w-]+-soft\b|\bbg-(?:red|amber|sky|emerald)-50\b",
                                                             classes)
            if tinted and "rounded-md" in classes.split():
                found.append(f"{source.name}: {classes[:80]}")
    assert found == []


def test_the_feather_list_names_only_icons_the_panel_uses() -> None:
    """Owner, 2026-10-30 (d-7985ee-c0cbfb): the list stays true when an icon is dropped, not only added."""
    licenses = (web.STATIC_DIR / "LICENSES.html").read_text()
    paragraph = licenses[licenses.index("Feather"):]
    paragraph = paragraph[:paragraph.index("</li>")]
    named = set(re.findall(r"<code>([a-z0-9-]+)</code>", paragraph))
    assert named == set(ICONS.read_text().split()) & FEATHER
