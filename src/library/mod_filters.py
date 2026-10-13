"""
The filters of a pack's mod list (A6, A12; owner, 2026-10-13): a name and four lists (side, channel,
pin, origin) that combine. Each option of a list counts the rows the other filters leave, without
its own: the number says what choosing it shows. Pure: rows are any object with the fields of
`FilterableRow` (the service's ModRow).
"""
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields
from typing import Optional, Protocol, TypeVar

# The longest name filter kept; the rest is dropped.
NAME_MAX = 100


class FilterableRow(Protocol):
    """Read only: a frozen dataclass row satisfies it."""
    @property
    def project_id(self) -> str: ...
    @property
    def title(self) -> str: ...
    @property
    def side(self) -> Optional[str]: ...
    @property
    def origin(self) -> Optional[str]: ...
    @property
    def pinned(self) -> bool: ...
    @property
    def pin_floor(self) -> Optional[str]: ...


Row = TypeVar("Row", bound=FilterableRow)
Test = Callable[[FilterableRow, Mapping[str, str]], bool]

# Each list's values, in the order its select shows them, and what keeps a row. "" is every row.
OPTIONS: dict[str, dict[str, Test]] = {
    "lado": {
        "decidir": lambda r, e: r.side == "admin",
        "servidor": lambda r, e: r.side == "include",
        "desactivado": lambda r, e: r.side == "exclude",
    },
    "canal": {
        "pack": lambda r, e: r.project_id not in e,
        "excepcion": lambda r, e: r.project_id in e,
        "beta": lambda r, e: e.get(r.project_id) == "beta",
        "alpha": lambda r, e: e.get(r.project_id) == "alpha",
    },
    "fijacion": {
        "fijados": lambda r, e: r.pinned,
        "sin": lambda r, e: not r.pinned,
        "baja": lambda r, e: r.pin_floor is not None,
    },
    "origen": {
        "agregados": lambda r, e: r.origin == "admin",
        "mrpack": lambda r, e: r.origin == "pack",
        "dependencias": lambda r, e: r.origin is None,
    },
}


def _folded(text: str) -> str:
    """Lower case, without accents, without surrounding spaces."""
    decomposed = unicodedata.normalize("NFKD", text.strip().casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


@dataclass(frozen=True)
class ModFilters:
    nombre: str = ""
    lado: str = ""
    canal: str = ""
    fijacion: str = ""
    origen: str = ""

    @classmethod
    def parse(cls, query: Mapping[str, str]) -> "ModFilters":
        """From a query string: an unknown value (or list) is dropped, never an error."""
        values = {name: query.get(name, "") for name in OPTIONS if query.get(name, "") in OPTIONS[name]}
        return cls(nombre=query.get("nombre", "").strip()[:NAME_MAX], **values)

    @property
    def active(self) -> bool:
        return any(getattr(self, f.name) for f in fields(self))

    def keeps(self, row: FilterableRow, exceptions: Mapping[str, str], skip: str = "") -> bool:
        """Whether every filter but `skip` keeps the row."""
        if skip != "nombre" and self.nombre and _folded(self.nombre) not in _folded(row.title):
            return False
        for name, options in OPTIONS.items():
            value = getattr(self, name)
            if name != skip and value and not options[value](row, exceptions):
                return False
        return True


def apply(rows: Sequence[Row], filters: ModFilters, exceptions: Mapping[str, str]) -> list[Row]:
    """The rows every filter keeps, in their order."""
    return [r for r in rows if filters.keeps(r, exceptions)]


def counts(rows: Sequence[FilterableRow], filters: ModFilters, exceptions: Mapping[str, str]) -> dict[str, dict[str, int]]:
    """Per list, per value ("" for every row): the rows the other filters keep that it keeps."""
    found: dict[str, dict[str, int]] = {}
    for name, options in OPTIONS.items():
        others = [r for r in rows if filters.keeps(r, exceptions, skip=name)]
        found[name] = {"": len(others)} | {value: sum(1 for r in others if test(r, exceptions))
                                            for value, test in options.items()}
    return found
