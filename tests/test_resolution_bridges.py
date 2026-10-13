"""Resolving with a bridge between loaders (batch C; d-7985ee-09c74f, d-7985ee-6ebc03, d-7985ee-4fd88e):
the native loaders stay the default, a mod with no native version may run through an active bridge,
its guest versions are asked for only then, and a native version always wins."""
from typing import Any

from src.library.resolution import Catalog, Target, resolve
from tests.test_resolution import cand, catalog, chosen, request

NEO = Target(game_version="1.21.1", loader="neoforge")
BRIDGED = Target(game_version="1.21.1", loader="neoforge", bridges=frozenset({"connector"}))


def neo(project: str, version: str, **kw: Any) -> Any:
    return cand(project, version, game="1.21.1", loader="neoforge", **kw)


def fab(project: str, version: str, **kw: Any) -> Any:
    return cand(project, version, game="1.21.1", loader="fabric", **kw)


def loaded(*candidates: Any, bridged: tuple[str, ...] = ()) -> Catalog:
    found = catalog(*candidates)
    found.bridged_loaded |= set(bridged)
    return found


def test_without_a_bridge_a_fabric_version_does_not_fit_a_neoforge_pack() -> None:
    lock = resolve(request("a", target=NEO), loaded(fab("a", "f1"), bridged=("a",)))
    assert chosen(lock) == {} and [p.kind for p in lock.problems] == ["unresolvable"]
    assert NEO.loaders("mod") == NEO.loaders_with_bridges("mod") == {"neoforge"}


def test_a_mod_with_no_native_version_asks_for_its_bridged_versions_then_runs_through_the_bridge() -> None:
    lock = resolve(request("a", target=BRIDGED), loaded(fab("a", "f1")))
    assert not lock.complete and lock.needs_bridged == {"a"}
    lock = resolve(request("a", target=BRIDGED), loaded(fab("a", "f1"), bridged=("a",)))
    assert chosen(lock) == {"a": "f1"} and lock.entries[0].via == "connector"


def test_a_native_version_wins_over_a_newer_bridged_one_and_asks_for_nothing_more() -> None:
    lock = resolve(request("a", target=BRIDGED), loaded(neo("a", "n1", days=1), fab("a", "f9", days=9)))
    assert lock.complete and chosen(lock) == {"a": "n1"} and lock.entries[0].via is None


def test_a_jar_for_both_loaders_is_native() -> None:
    shared = fab("a", "both").model_copy(update={"loaders": ["fabric", "neoforge"]})
    lock = resolve(request("a", target=BRIDGED), loaded(shared))
    assert chosen(lock) == {"a": "both"} and lock.entries[0].via is None


def test_a_bridge_that_does_not_cover_the_version_does_nothing() -> None:
    old = Target(game_version="1.20.1", loader="neoforge", bridges=frozenset({"connector"}))
    lock = resolve(request("a", target=old), loaded(cand("a", "f1", game="1.20.1", loader="fabric")))
    assert chosen(lock) == {} and lock.needs_bridged == set()
    assert old.loaders_with_bridges("mod") == {"neoforge"}


def test_only_mods_widen_shaders_keep_their_own_loaders() -> None:
    assert BRIDGED.loaders_with_bridges("mod") == {"neoforge", "fabric"}
    assert BRIDGED.loaders_with_bridges("shader") == NEO.loaders("shader")


def test_a_suggestion_that_only_runs_through_the_bridge_is_not_offered() -> None:
    parent = neo("a", "n1", deps=(("c", None, "optional"), ("d", None, "optional")))
    lock = resolve(request("a", target=BRIDGED), loaded(parent, fab("c", "c1"), neo("d", "d1"), bridged=("c",)))
    assert [s.project_id for s in lock.suggestions] == ["d"]
