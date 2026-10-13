"""The audit log in Spanish (A9, spec §6): every action the code records has its text, and a detail
reads as a phrase, with the catalog's name next to each id; what has no label is shown raw."""
import re
from pathlib import Path

import pytest

from src.web.audit_labels import ACTIONS, AREAS, DETAILS, detail_text, parse_target, target_label


def test_every_recorded_action_has_a_spanish_text() -> None:
    recorded: set[str] = set()
    for path in Path("src").rglob("*.py"):
        recorded |= set(re.findall(r'audit\.record\([^,]+,\s*"([a-z_.]+)"', path.read_text()))
    assert len(recorded) >= 30
    assert sorted(recorded - set(ACTIONS)) == []


def test_each_action_belongs_to_an_area() -> None:
    assert {area for _, area in ACTIONS.values()} <= set(AREAS)
    assert ACTIONS["bundle.pin"] == ("Fijó una versión", "Packs")
    assert ACTIONS["server.rollback"] == ("Hizo rollback", "Servidores")
    assert ACTIONS["replacement.set"][1] == "Reemplazos" and ACTIONS["build.create"][1] == "Builds"


def test_the_spec_s_detail_keys_have_their_labels() -> None:
    for key, label in {"project_id": "Mod", "version_id": "Versión", "server": "En el servidor",
                       "decisions": "Decisiones", "channel": "Canal", "downgrade": "Baja",
                       "cleared": "Limpiado", "number": "Build", "dry_run": "Simulación"}.items():
        assert DETAILS[key] == label


@pytest.mark.parametrize("key, value, expected", [
    ("project_id", "J", "JEI (J)"),
    ("project_id", "ZZZ", "ZZZ"),
    ("version_id", "V", "19.60 (V)"),
    ("version_id", None, "—"),
    ("replacement", "J", "JEI (J)"),
    ("server", True, "sí"),
    ("dry_run", False, "no"),
    ("downgrade", {"from": "19.57", "to": "19.51"}, "19.57 → 19.51, confirmada"),
    ("decisions", {"J": True, "ZZZ": False}, "JEI: sí, ZZZ: no"),
    ("channel_exceptions", {"J": "beta"}, "JEI: beta"),
    ("refs", ["J", "sodium"], "JEI, sodium"),
    ("cleared", {}, "—"),
    ("foo", 1, "1"),
])
def test_a_detail_reads_as_a_phrase(key: str, value: object, expected: str) -> None:
    assert detail_text(key, value, {"J": "JEI", "V": "19.60"}) == expected


def test_a_target_reads_by_its_kind_and_name_or_its_id() -> None:
    assert target_label("bundle", "b1", "Survival") == "Pack «Survival»"
    assert target_label("server", "s1", None) == "Servidor s1"
    assert target_label("job", "j1", "") == "job j1"


@pytest.mark.parametrize("text,expected", [
    ("Survival · pack", (None, "Survival", "bundle")),
    ("Lobby · servidor", (None, "Lobby", "server")),
    ("a · b · pack", (None, "a · b", "bundle")),
    ("bundle:b1", (("bundle", "b1"), None, None)),
    ("hora:12", (None, "hora:12", None)),
    ("Survival · cosa", (None, "Survival · cosa", None)),
    ("Survival", (None, "Survival", None)),
    ("", (None, None, None)),
])
def test_a_chosen_target_reads_back_as_a_filter(text: str, expected: tuple[object, ...]) -> None:
    """The owner, 2026-10-13 (minor 7): the field holds «name · kind»; `kind:id` (old links) still
    works, only for a kind the panel knows, so a name with a colon is still a name."""
    assert parse_target(text) == expected
