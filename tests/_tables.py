"""The tables of a rendered page, read with the standard library's parser (B4, d-7985ee-937ca8): each
cell's `data-label` must be its column's header, so the row reads as a card below `sm:`."""
from dataclasses import dataclass, field
from html.parser import HTMLParser

# What stacks a table below `sm:` (src/web/templates/_macros.html, `ui.table`).
STACKS = "max-sm:[&>tr>td]:before:content-[attr(data-label)]"


@dataclass
class Table:
    classes: str = ""
    body_classes: str = ""
    headers: list[str] = field(default_factory=list)
    rows: list[list[dict[str, str]]] = field(default_factory=list)


class _Tables(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[Table] = []
        self.header: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k: v or "" for k, v in attrs}
        if tag == "table":
            self.tables.append(Table(classes=values.get("class", "")))
        elif not self.tables:
            return
        elif tag == "tbody":
            self.tables[-1].body_classes = values.get("class", "")
        elif tag == "th":
            self.header = []
        elif tag == "tr" and self.tables[-1].body_classes:
            self.tables[-1].rows.append([])
        elif tag == "td":
            self.tables[-1].rows[-1].append(values)

    def handle_endtag(self, tag: str) -> None:
        if tag == "th" and self.header is not None:
            self.tables[-1].headers.append(" ".join("".join(self.header).split()))
            self.header = None

    def handle_data(self, data: str) -> None:
        if self.header is not None:
            self.header.append(data)


def tables(html: str) -> list[Table]:
    parser = _Tables()
    parser.feed(html)
    return parser.tables


def assert_cells_labelled(html: str) -> int:
    """Every table stacks below `sm:` and every cell (but the empty message, which spans the row)
    carries its column's header as `data-label`. Returns how many cells were checked."""
    found = tables(html)
    assert found, "no table on the page"
    checked = 0
    for table in found:
        assert STACKS in table.body_classes, f"table {table.headers} does not stack below sm:"
        for row in table.rows:
            for index, cell in enumerate(row):
                if "colspan" in cell:
                    continue
                assert cell.get("data-label") == table.headers[index], \
                    f"cell {index} of {table.headers}: data-label={cell.get('data-label')!r}"
                checked += 1
    return checked
