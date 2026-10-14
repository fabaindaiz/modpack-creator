"""Resolving with a bridge between loaders (batch C; d-7985ee-09c74f, d-7985ee-6ebc03, d-7985ee-4fd88e):
the native loaders stay the default, a mod with no native version may run through an active bridge,
its guest versions are asked for only then, and a native version always wins."""
from typing import Any

from src.library.bridges import CONNECTOR, FABRIC_API, FORGIFIED_FABRIC_API
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


# Task 3: the way a mod is installed, its dependencies, and what the bridge needs.



def test_an_installed_bridged_mod_stays_bridged_and_says_a_native_caught_up() -> None:
    """d-7985ee-7e602f, d-7985ee-dea282: the resolution never changes a mod's way by itself."""
    versions = [fab("a", "f1", days=1), fab("a", "f2", days=3), neo("a", "n2", days=5)]
    lock = resolve(request("a", target=BRIDGED, floors={"a": ["f1"]}), loaded(*versions, bridged=("a",)))
    assert chosen(lock) == {"a": "f2"} and lock.entries[0].via == "connector"
    assert lock.native_available == {"a": "n2"} and lock.native_behind == {}


def test_a_native_version_older_than_the_installed_one_is_behind() -> None:
    versions = [neo("a", "n0", days=0), fab("a", "f1", days=1), fab("a", "f2", days=3)]
    lock = resolve(request("a", target=BRIDGED, floors={"a": ["f1"]}), loaded(*versions, bridged=("a",)))
    assert chosen(lock) == {"a": "f2"} and lock.native_behind == {"a": "n0"} and lock.native_available == {}


def test_an_installed_native_mod_never_becomes_bridged() -> None:
    versions = [neo("a", "n1", days=1), fab("a", "f9", days=9)]
    lock = resolve(request("a", target=BRIDGED, floors={"a": ["n1"]}), loaded(*versions, bridged=("a",)))
    assert chosen(lock) == {"a": "n1"} and lock.entries[0].via is None


def test_a_mod_marked_native_only_takes_its_native_versions() -> None:
    """d-7985ee-8e5f9e: what the admin accepted with «Pasar a nativa»."""
    versions = [fab("a", "f1", days=1), fab("a", "f2", days=3), neo("a", "n2", days=5)]
    lock = resolve(request("a", target=BRIDGED, floors={"a": ["f1"]}, native_only={"a"}), loaded(*versions, bridged=("a",)))
    assert chosen(lock) == {"a": "n2"} and lock.entries[0].via is None and lock.native_available == {}


def test_a_native_mod_s_dependency_that_only_runs_through_the_bridge_is_a_problem_not_added() -> None:
    """d-7985ee-b921b2."""
    parent = neo("p", "p1", deps=(("lib", None, "required"),))
    lock = resolve(request("p", target=BRIDGED), loaded(parent, fab("lib", "l1"), bridged=("lib",)))
    assert chosen(lock) == {"p": "p1"}
    [problem] = [x for x in lock.problems if x.kind == "bridge_dependency"]
    assert problem.project_id == "lib" and problem.requested_by == ["p"]


def test_a_bridged_mod_s_dependencies_run_through_the_bridge() -> None:
    """d-7985ee-b7bddf."""
    parent = fab("b", "b1", deps=(("lib", None, "required"),))
    lock = resolve(request("b", target=BRIDGED), loaded(parent, fab("lib", "l1"), bridged=("b", "lib")))
    assert chosen(lock)["lib"] == "l1" and {e.project_id: e.via for e in lock.entries}["lib"] == "connector"


def test_with_connector_fabric_api_is_forgified_fabric_api_and_the_bridge_is_asked_for() -> None:
    """d-7985ee-d7fcae, d-7985ee-80ee9a: the resolution never adds the bridge itself."""
    parent = fab("b", "b1", deps=((FABRIC_API, None, "required"),))
    ffapi = neo(FORGIFIED_FABRIC_API, "ff1")
    lock = resolve(request("b", target=BRIDGED), loaded(parent, ffapi, bridged=("b",)))
    assert FORGIFIED_FABRIC_API in chosen(lock) and FABRIC_API not in chosen(lock)
    missing = [x.project_id for x in lock.problems if x.kind == "bridge_missing"]
    assert missing == [CONNECTOR]


def test_fabric_api_in_a_pack_with_connector_is_a_conflict() -> None:
    lock = resolve(request(FABRIC_API, target=BRIDGED), loaded(fab(FABRIC_API, "fa1"), bridged=(FABRIC_API,)))
    assert [x.kind for x in lock.problems if x.project_id == FABRIC_API] == ["bridge_conflict"]
