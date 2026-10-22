"""Las tablas del panel en el móvil (B4, d-7985ee-937ca8): cada celda de cada `ui.table` lleva
`data-label` igual al encabezado de su columna, y bajo `sm:` la fila se apila como tarjeta.

Aquí se leen las plantillas, todas las ramas de cada fila; las páginas renderizadas se revisan en
sus propios tests con `tests/_tables.py`."""
import ast
import re
from pathlib import Path

from src.web import templates
from tests._tables import STACKS, assert_cells_labelled

TEMPLATES = Path(__file__).resolve().parent.parent / "src" / "web" / "templates"
CALL = re.compile(r"\{%-?\s*call\b[^%]*?ui\.table\(\s*(\[[^\]]*\])", re.S)
TAG = re.compile(r"\{%-?\s*(call|endcall)\b")
ROW = re.compile(r"<tr\b.*?</tr>", re.S)
CELL = re.compile(r"<td\b([^>]*)>")


def table_blocks() -> list[tuple[str, list[str], str]]:
    """Every `ui.table` call of the templates: its file, headers and call block."""
    found = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        text = path.read_text()
        for match in CALL.finditer(text):
            depth, end = 0, match.start()
            for tag in TAG.finditer(text, match.start()):
                depth += 1 if tag.group(1) == "call" else -1
                if depth == 0:
                    end = tag.start()
                    break
            found.append((str(path.relative_to(TEMPLATES)), ast.literal_eval(match.group(1)), text[match.end():end]))
    return found


def test_every_table_template_is_found() -> None:
    names = {name for name, _, _ in table_blocks()}
    assert names == {"modpacks/_local_files.html", "modpacks/list.html", "servers/list.html", "modpacks/_builds.html",
                     "modpacks/_replacements.html", "mods/_versions.html", "servers/_compare.html"}


def test_every_cell_of_every_table_template_has_its_header_as_label() -> None:
    for name, headers, block in table_blocks():
        rows = ROW.findall(block)
        assert rows, f"{name}: no rows"
        for row in rows:
            cells = CELL.findall(row)
            assert len(cells) == len(headers), f"{name}: {len(cells)} cells for {headers}"
            for header, attrs in zip(headers, cells):
                assert f'data-label="{header}"' in attrs, f"{name}: a cell of {header!r} without its label"


def test_the_table_stacks_below_sm_and_is_a_table_from_sm() -> None:
    html = templates.env.from_string(
        '{% import "_macros.html" as ui %}{% call ui.table(["Nombre", ""]) %}'
        '<tr><td data-label="Nombre">x</td><td data-label="">y</td></tr>{% endcall %}').render()
    assert assert_cells_labelled(html) == 2
    assert STACKS in html
    assert re.search(r'<table class="[^"]*max-sm:block', html)
    assert re.search(r'<thead class="[^"]*max-sm:sr-only', html)
    # Only below sm: nothing of the stacking applies from sm on.
    body = re.search(r'<tbody class="([^"]*)"', html)
    assert body and "max-sm:[&>tr]:block" in body.group(1)
    assert all(c.startswith(("max-sm:", "dark:max-sm:", "divide-", "dark:divide-", "[&>tr>td]:"))
               for c in body.group(1).split())


def test_the_table_styles_only_its_own_rows_and_cells() -> None:
    """Review of batch B, minor 7: `[&_td]` reached the cells of a table nested in a cell too; the
    body's variants name its own rows and their cells (`>`), never every descendant."""
    macros = (Path(__file__).resolve().parent.parent / "src/web/templates/_macros.html").read_text()
    body = re.search(r'<tbody class="([^"]*)"', macros)
    assert body
    assert "[&_td" not in body.group(1) and "[&_tr" not in body.group(1)
    assert "[&>tr>td]:px-3" in body.group(1)


def test_an_empty_table_has_no_labelled_cell_but_still_stacks() -> None:
    html = templates.env.from_string(
        '{% import "_macros.html" as ui %}{% call ui.table(["Nombre"], empty="Nada.") %}{% endcall %}').render()
    assert assert_cells_labelled(html) == 0 and "Nada." in html
