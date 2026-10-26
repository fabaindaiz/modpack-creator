"""The filters of a pack's mod list (A6, A12; owner, 2026-10-13): name, side, channel, pin and
origin, combined; each option counts with the other filters applied and not its own."""
from dataclasses import dataclass
from typing import Optional

import pytest

from src.library.mod_filters import ModFilters, apply, counts


@dataclass
class Row:
    project_id: str
    title: str
    side: Optional[str] = "include"
    origin: Optional[str] = "admin"
    pinned: bool = False
    pin_floor: Optional[str] = None
    via: Optional[str] = None
    pin_origins: tuple = ()
    off_tags: bool = False


ROWS = [
    Row("S", "Sódium"),
    Row("L", "Lithium", side="exclude", pinned=True),
    Row("I", "Iris", side="admin", origin="pack"),
    Row("F", "Fabric API", origin=None, pinned=True, pin_floor="0.9"),
]
BRIDGED = [Row("X", "Xaero"), Row("M", "FabricMap", via="connector")]
EXCEPTIONS = {"S": "beta", "I": "alpha"}


def ids(filters: ModFilters) -> list[str]:
    return [r.project_id for r in apply(ROWS, filters, EXCEPTIONS)]


@pytest.mark.parametrize("filters,expected", [
    (ModFilters(), ["S", "L", "I", "F"]),
    (ModFilters(lado="decidir"), ["I"]),
    (ModFilters(lado="servidor"), ["S", "F"]),
    (ModFilters(lado="desactivado"), ["L"]),
    (ModFilters(canal="pack"), ["L", "F"]),
    (ModFilters(canal="excepcion"), ["S", "I"]),
    (ModFilters(canal="beta"), ["S"]),
    (ModFilters(canal="alpha"), ["I"]),
    (ModFilters(fijacion="fijados"), ["L", "F"]),
    (ModFilters(fijacion="sin"), ["S", "I"]),
    (ModFilters(fijacion="baja"), ["F"]),
    (ModFilters(origen="agregados"), ["S", "L"]),
    (ModFilters(origen="mrpack"), ["I"]),
    (ModFilters(origen="dependencias"), ["F"]),
])
def test_each_filter_keeps_its_rows(filters: ModFilters, expected: list[str]) -> None:
    assert ids(filters) == expected


def test_filters_combine() -> None:
    assert ids(ModFilters(fijacion="fijados", lado="desactivado")) == ["L"]
    assert ids(ModFilters(fijacion="fijados", origen="mrpack")) == []


def test_name_matches_without_case_or_accents() -> None:
    assert ids(ModFilters(nombre="sodium")) == ["S"]
    assert ids(ModFilters(nombre="  API ")) == ["F"]


def test_counts_respect_the_other_filters_not_their_own() -> None:
    found = counts(ROWS, ModFilters(fijacion="fijados"), EXCEPTIONS)
    # The pin filter counts over every row (its own is not applied)...
    assert found["fijacion"] == {"": 4, "fijados": 2, "sin": 2, "baja": 1}
    # ...the others over the pinned ones only.
    assert found["lado"] == {"": 2, "decidir": 0, "servidor": 1, "desactivado": 1}
    assert found["canal"] == {"": 2, "pack": 2, "excepcion": 0, "beta": 0, "alpha": 0}
    assert found["origen"] == {"": 2, "agregados": 1, "mrpack": 0, "dependencias": 1}


def test_unknown_values_are_dropped() -> None:
    parsed = ModFilters.parse({"lado": "xyz", "canal": "BETA", "fijacion": "baja", "origen": "", "nombre": "x" * 500,
                               "otro": "1"})
    assert parsed == ModFilters(nombre="x" * 100, fijacion="baja")
    assert parsed.active and not ModFilters().active


def test_the_loader_filter_splits_native_and_bridged() -> None:
    """Batch C (d-7985ee-533aff)."""
    assert [r.project_id for r in apply(BRIDGED, ModFilters(loader="nativos"), {})] == ["X"]
    assert [r.project_id for r in apply(BRIDGED, ModFilters(loader="puente"), {})] == ["M"]
    assert counts(BRIDGED, ModFilters(), {})["loader"] == {"": 2, "nativos": 1, "puente": 1}


def test_the_version_filter_finds_what_an_import_left_offered_or_off_the_tags() -> None:
    """O9 D9 (owner, 2026-10-26): the pack's version offered on a card, and one used off the tags."""
    from types import SimpleNamespace

    rows = [Row("A", "A", pin_origins=(SimpleNamespace(source="pack", in_use=False),)),
            Row("B", "B", off_tags=True),
            Row("C", "C", pin_origins=(SimpleNamespace(source="pack", in_use=True),))]
    assert [r.project_id for r in apply(rows, ModFilters(version="ofrecida"), {})] == ["A"]
    assert [r.project_id for r in apply(rows, ModFilters(version="fuera"), {})] == ["B"]
    assert ModFilters.parse({"version": "ofrecida"}).version == "ofrecida"
    assert counts(rows, ModFilters(), {})["version"] == {"": 3, "ofrecida": 1, "fuera": 1}
