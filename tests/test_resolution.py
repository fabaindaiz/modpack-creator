"""Resolver un modpack (i-7985ee-7f735a): una función pura de pedido y catálogo a lock.

El algoritmo es el de `docs/research/2026-10-resolucion-y-formatos.md` §5, con las decisiones
d-7985ee-86fffa (reemplazos globales y por pack), d-7985ee-20b06b (un conflicto se reporta, sin
backtracking) y d-7985ee-f0c7a6 (lo ambiguo en el servidor lo decide el admin).

Las versiones de Modrinth se leen de respuestas reales grabadas (tests/fixtures/modrinth/); las
reglas se prueban con candidatos armados a mano, que no dependen de la forma de la API.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.library.resolution import (
    Candidate,
    CandidateDependency,
    CandidateFile,
    Catalog,
    Problem,
    ResolveRequest,
    Target,
    merge_replacements,
    request_digest,
    resolve,
    server_inclusion,
)

FIXTURES = Path(__file__).parent / "fixtures" / "modrinth"
SODIUM, IRIS = "AANobbMI", "YL57xq9U"
EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def recorded() -> list[Candidate]:
    return [Candidate.from_modrinth(v) for f in sorted(FIXTURES.glob("versions-*.json")) for v in load(f.name)]


def catalog(*candidates: Candidate) -> Catalog:
    return Catalog.of(candidates)


def cand(project: str, version: str, *, days: int = 0, type: str = "release", game: str = "26.3",
         loader: str = "fabric", env: str | None = "client_and_server", status: str = "listed",
         deps: tuple[tuple[str | None, str | None, str], ...] = ()) -> Candidate:
    return Candidate(
        project_id=project, version_id=version, version_number=version, version_type=type,
        status=status, date_published=EPOCH + timedelta(days=days), game_versions=[game],
        loaders=[loader], environment=env,
        dependencies=[CandidateDependency(project_id=p, version_id=v, file_name=None, dependency_type=t)
                      for p, v, t in deps],
        file=CandidateFile(filename=f"{version}.jar", url=f"https://cdn.modrinth.com/{version}.jar",
                           sha1=version, sha512=version * 2, size=1))


def request(*projects: str, **overrides: Any) -> ResolveRequest:
    fields: dict[str, Any] = {"target": Target(game_version="26.3", loader="fabric"), "projects": list(projects)}
    fields.update(overrides)
    return ResolveRequest(**fields)


def chosen(lock: Any) -> dict[str, str]:
    return {e.project_id: e.version_id for e in lock.entries}


# Con respuestas reales.


def test_every_recorded_version_reads_as_a_candidate() -> None:
    candidates = recorded()
    assert candidates and all(c.file and c.file.sha512 and c.file.url.startswith("https://") for c in candidates)


def test_an_exact_dependency_of_unknown_type_asks_only_for_its_project() -> None:
    iris = [c for c in recorded() if c.project_id == IRIS or c.version_id == "bAZQdGpg"]
    lock = resolve(request(IRIS, target=Target(game_version="26.3", loader="fabric",
                                               channels=frozenset({"release", "beta", "alpha"}))),
                   Catalog.of(iris, types={IRIS: "mod"}))
    assert (lock.needs_projects, lock.needs_types, lock.needs_versions) == (set(), {SODIUM}, set())


def test_iris_for_26_3_brings_the_exact_sodium_it_pins() -> None:
    lock = resolve(request(IRIS, target=Target(game_version="26.3", loader="fabric",
                                               channels=frozenset({"release", "beta", "alpha"}))),
                   catalog(*recorded()))
    assert lock.complete and not lock.problems
    # bAZQdGpg, la que fija esta Iris; no v4PSXean, la más nueva de Sodium para 26.3.
    assert chosen(lock)[SODIUM] == "bAZQdGpg"
    sodium = next(e for e in lock.entries if e.project_id == SODIUM)
    assert sodium.requested_by == [IRIS] and "requerida" in sodium.reason
    # Las dos son de cliente: van en el lock, no en el servidor.
    assert {e.server for e in lock.entries} == {"exclude"}


def test_what_the_catalog_lacks_is_asked_for_not_guessed() -> None:
    iris = [c for c in recorded() if c.project_id == IRIS]
    lock = resolve(request(IRIS, target=Target(game_version="26.3", loader="fabric",
                                               channels=frozenset({"release", "beta", "alpha"}))),
                   catalog(*iris))
    assert not lock.complete
    assert lock.needs_versions == {"bAZQdGpg"}


# Candidatos.


def test_the_best_candidate_is_the_newest_of_the_most_stable_allowed_channel() -> None:
    versions = [cand("a", "old", days=1), cand("a", "new", days=2), cand("a", "beta", days=3, type="beta")]
    assert chosen(resolve(request("a"), catalog(*versions))) == {"a": "new"}
    allow_beta = Target(game_version="26.3", loader="fabric", channels=frozenset({"release", "beta"}))
    assert chosen(resolve(request("a", target=allow_beta), catalog(*versions))) == {"a": "new"}
    only_beta = [cand("a", "beta", type="beta")]
    assert chosen(resolve(request("a", target=allow_beta), catalog(*only_beta))) == {"a": "beta"}


def test_versions_for_another_game_version_loader_or_status_are_not_candidates() -> None:
    versions = [cand("a", "other-game", game="1.21.11", days=3), cand("a", "forge", loader="forge", days=2),
                cand("a", "draft", status="draft", days=1), cand("a", "fits")]
    assert chosen(resolve(request("a"), catalog(*versions))) == {"a": "fits"}


def test_quilt_accepts_fabric_versions() -> None:
    quilt = Target(game_version="26.3", loader="quilt")
    assert chosen(resolve(request("a", target=quilt), catalog(cand("a", "fabric-build")))) == {"a": "fabric-build"}


def test_a_project_with_no_candidate_is_unresolvable() -> None:
    lock = resolve(request("a"), catalog(cand("a", "old", game="1.21.11")))
    assert [(p.kind, p.project_id) for p in lock.problems] == [("unresolvable", "a")]


# Pins y conflictos.


def test_an_admin_pin_wins_over_the_best_candidate() -> None:
    versions = [cand("a", "old", days=1), cand("a", "new", days=2)]
    assert chosen(resolve(request("a", pins={"a": "old"}), catalog(*versions))) == {"a": "old"}


def test_a_pin_that_does_not_fit_the_target_is_a_problem() -> None:
    lock = resolve(request("a", pins={"a": "old"}), catalog(cand("a", "old", game="1.21.11"), cand("a", "new")))
    assert [(p.kind, p.project_id, p.pinned) for p in lock.problems] == [("pin_mismatch", "a", True)]


def test_two_exact_requirements_that_differ_are_reported_not_solved() -> None:
    versions = [cand("b", "b", deps=(("dep", "v1", "required"),)), cand("c", "c", deps=(("dep", "v2", "required"),)),
                cand("dep", "v1", days=1), cand("dep", "v2", days=2)]
    lock = resolve(request("b", "c"), catalog(*versions))
    [problem] = lock.problems
    assert (problem.kind, problem.project_id, problem.pinned) == ("pin_conflict", "dep", False)
    assert "v1" in problem.detail and "v2" in problem.detail and "b" in problem.detail and "c" in problem.detail


def test_a_free_choice_gives_way_to_an_exact_requirement_found_later() -> None:
    versions = [cand("dep", "v1", days=1), cand("dep", "v2", days=2),
                cand("b", "b", deps=(("dep", "v1", "required"),))]
    lock = resolve(request("dep", "b"), catalog(*versions))
    assert not lock.problems
    assert chosen(lock) == {"dep": "v1", "b": "b"}


def test_an_admin_pin_against_an_exact_requirement_is_a_conflict() -> None:
    versions = [cand("dep", "v1", days=1), cand("dep", "v2", days=2),
                cand("b", "b", deps=(("dep", "v1", "required"),))]
    lock = resolve(request("dep", "b", pins={"dep": "v2"}), catalog(*versions))
    assert [(p.kind, p.project_id, p.pinned) for p in lock.problems] == [("pin_conflict", "dep", True)]


def test_an_admin_pin_modrinth_no_longer_has_is_the_pins_problem() -> None:
    versions = catalog(cand("a", "new"))
    versions.gone.add("deleted")
    lock = resolve(request("a", pins={"a": "deleted"}), versions)
    assert [(p.kind, p.project_id, p.pinned) for p in lock.problems] == [("unresolvable", "a", True)]


def test_a_dependency_asking_for_a_version_that_does_not_fit_is_not_the_pins_problem() -> None:
    # The admin pinned dep to a version that works; b asks for another that does not fit. Fixing
    # the pin does not help: the card must not blame it (F2 of the final review).
    versions = [cand("dep", "new"), cand("dep", "old", game="1.21.11"),
                cand("b", "b", deps=(("dep", "old", "required"),))]
    lock = resolve(request("dep", "b", pins={"dep": "new"}), catalog(*versions))
    assert [(p.kind, p.project_id, p.pinned, p.requested_by) for p in lock.problems] == [
        ("pin_mismatch", "dep", False, ["b"])]


# Dependencias.


def test_a_required_project_dependency_takes_its_best_candidate() -> None:
    versions = [cand("a", "a", deps=(("dep", None, "required"),)), cand("dep", "d1", days=1), cand("dep", "d2", days=2)]
    lock = resolve(request("a"), catalog(*versions))
    assert chosen(lock) == {"a": "a", "dep": "d2"}


def test_a_dependency_without_ids_is_unresolvable() -> None:
    versions = [cand("a", "a", deps=((None, None, "required"),))]
    assert [p.kind for p in resolve(request("a"), catalog(*versions)).problems] == ["unresolvable"]


def test_optional_dependencies_are_suggested_and_embedded_ones_are_not_downloaded() -> None:
    versions = [cand("a", "a", deps=(("opt", None, "optional"), ("emb", None, "embedded"),)), cand("opt", "o")]
    lock = resolve(request("a"), catalog(*versions))
    assert chosen(lock) == {"a": "a"}
    assert [(s.project_id, s.requested_by) for s in lock.suggestions] == [("opt", ["a"])]


def test_incompatible_projects_both_chosen_are_reported() -> None:
    versions = [cand("a", "a", deps=(("b", None, "incompatible"),)), cand("b", "b")]
    lock = resolve(request("a", "b"), catalog(*versions))
    assert [(p.kind, p.project_id) for p in lock.problems] == [("incompatible", "b")]


# Reemplazos (d-7985ee-86fffa).


def test_a_replacement_takes_the_place_of_a_dependency() -> None:
    versions = [cand("a", "a", loader="quilt", deps=(("fabric-api", None, "required"),)),
                cand("fabric-api", "f"), cand("qfapi", "q", loader="quilt")]
    lock = resolve(request("a", target=Target(game_version="26.3", loader="quilt"),
                           replacements={"fabric-api": "qfapi"}), catalog(*versions))
    assert chosen(lock) == {"a": "a", "qfapi": "q"}
    qfapi = next(e for e in lock.entries if e.project_id == "qfapi")
    assert "fabric-api" in qfapi.reason and qfapi.requested_by == ["a"]


def test_a_replacement_with_nothing_removes_the_dependency() -> None:
    versions = [cand("a", "a", deps=(("dep", None, "required"),)), cand("dep", "d")]
    lock = resolve(request("a", replacements={"dep": None}), catalog(*versions))
    assert chosen(lock) == {"a": "a"} and not lock.problems


def test_pack_rules_add_to_and_override_global_ones() -> None:
    merged = merge_replacements({"fabric-api": "qfapi", "x": "y"}, {"x": None, "z": "w"})
    assert merged == {"fabric-api": "qfapi", "x": None, "z": "w"}


# Lado servidor (d-7985ee-f0c7a6).


def test_server_inclusion_follows_modrinth_and_leaves_the_ambiguous_to_the_admin() -> None:
    assert {env: server_inclusion(env) for env in [
        "client_and_server", "server_only", "server_only_client_optional", "dedicated_server_only",
        "client_or_server_prefers_both", "client_only", "singleplayer_only",
        "client_or_server", "client_only_server_optional", "unknown", None]} == {
        "client_and_server": "include", "server_only": "include", "server_only_client_optional": "include",
        "dedicated_server_only": "include", "client_or_server_prefers_both": "include",
        "client_only": "exclude", "singleplayer_only": "exclude",
        "client_or_server": "admin", "client_only_server_optional": "admin", "unknown": "admin", None: "admin"}


def test_an_ambiguous_mod_waits_for_the_admin_and_then_follows_the_decision() -> None:
    versions = [cand("a", "a", env="client_or_server"), cand("b", "b", env=None)]
    lock = resolve(request("a", "b"), catalog(*versions))
    assert {e.project_id for e in lock.undecided} == {"a", "b"}
    decided = resolve(request("a", "b", server_decisions={"a": True, "b": False}), catalog(*versions))
    assert {e.project_id: e.server for e in decided.entries} == {"a": "include", "b": "exclude"}
    assert not decided.undecided


def test_a_version_modrinth_no_longer_has_is_unresolvable_not_asked_for_again() -> None:
    versions = catalog(cand("a", "a", deps=(("dep", "deleted", "required"),)))
    versions.gone.add("deleted")
    lock = resolve(request("a"), versions)
    assert lock.complete
    assert [(p.kind, p.project_id, p.pinned) for p in lock.problems] == [("unresolvable", "dep", False)]


# Tipos de proyecto (d-7985ee-9170b8).

SHADER = "HVnmMxH1"


def test_a_shader_resolves_by_its_own_loaders_and_stays_off_the_server() -> None:
    shader = [c for c in recorded() if c.project_id == SHADER]
    lock = resolve(request(SHADER), Catalog.of(shader, types={SHADER: "shader"}))
    assert not lock.problems
    [entry] = lock.entries
    assert entry.version_number == "r5.9.3" and entry.server == "exclude"


def test_resourcepacks_go_to_the_client_and_datapacks_also_to_the_server() -> None:
    versions = [cand("rp", "rp", loader="minecraft", env=None), cand("dp", "dp", loader="datapack", env=None)]
    lock = resolve(request("rp", "dp"), Catalog.of(versions, types={"rp": "resourcepack", "dp": "datapack"}))
    assert not lock.problems
    assert {e.project_id: e.server for e in lock.entries} == {"rp": "exclude", "dp": "include"}


def test_a_mod_is_not_resolved_with_a_shader_loader() -> None:
    lock = resolve(request("a"), Catalog.of([cand("a", "a", loader="iris")], types={"a": "mod"}))
    assert [(p.kind, p.project_id) for p in lock.problems] == [("unresolvable", "a")]


def test_a_project_whose_type_is_unknown_is_asked_for() -> None:
    lock = resolve(request("a"), Catalog.of([cand("a", "a")], types={}))
    assert lock.needs_projects == {"a"}


def test_a_type_resolution_does_not_support_is_a_problem() -> None:
    lock = resolve(request("pack"), Catalog.of([cand("pack", "p", loader="mrpack")], types={"pack": "modpack"}))
    assert [(p.kind, p.project_id) for p in lock.problems] == [("unresolvable", "pack")]


def test_a_shader_without_iris_in_the_pack_suggests_iris() -> None:
    shader = [c for c in recorded() if c.project_id == SHADER]
    alone = resolve(request(SHADER), Catalog.of(shader, types={SHADER: "shader"}))
    assert [(s.project_id, s.requested_by) for s in alone.suggestions] == [(IRIS, [SHADER])]
    assert not alone.problems


def test_a_shader_with_iris_in_the_pack_suggests_nothing() -> None:
    versions = [c for c in recorded() if c.project_id in (SHADER, IRIS, SODIUM)]
    lock = resolve(request(SHADER, IRIS, target=Target(game_version="26.3", loader="fabric",
                                                       channels=frozenset({"release", "beta", "alpha"}))),
                   Catalog.of(versions, types={SHADER: "shader", IRIS: "mod", SODIUM: "mod"}))
    assert lock.suggestions == [] and not lock.problems


def test_each_lock_entry_keeps_its_project_type() -> None:
    versions = [cand("rp", "rp", loader="minecraft"), cand("a", "a")]
    lock = resolve(request("rp", "a"), Catalog.of(versions, types={"rp": "resourcepack", "a": "mod"}))
    assert {e.project_id: e.project_type for e in lock.entries} == {"rp": "resourcepack", "a": "mod"}


def digest_request(**overrides: Any) -> ResolveRequest:
    channels = overrides.pop("channels", None)
    fields: dict[str, Any] = {"target": Target(game_version="26.3", loader="fabric"), "projects": ["a", "b"]}
    if channels is not None:
        fields["target"] = Target(game_version="26.3", loader="fabric", channels=frozenset(channels))
    fields.update(overrides)
    return ResolveRequest(**fields)


def test_digest_ignores_order_and_follows_inputs() -> None:
    a = digest_request(projects=["b", "a"], pins={"a": "1", "b": "2"})
    b = digest_request(projects=["a", "b"], pins={"b": "2", "a": "1"})
    assert request_digest(a) == request_digest(b)
    assert len(request_digest(a)) == 64
    for changed in (digest_request(projects=["a"], pins={"a": "1", "b": "2"}),
                    digest_request(projects=["a", "b"], pins={"a": "9", "b": "2"}),
                    digest_request(projects=["a", "b"], pins={"a": "1", "b": "2"}, server_decisions={"a": True}),
                    digest_request(projects=["a", "b"], pins={"a": "1", "b": "2"}, channels={"beta"}),
                    digest_request(projects=["a", "b"], pins={"a": "1", "b": "2"}, replacements={"a": None})):
        assert request_digest(changed) != request_digest(a)


def test_digest_ignores_channel_order() -> None:
    a = digest_request(channels=["release", "beta", "alpha"])
    b = digest_request(channels=["alpha", "beta", "release"])
    assert request_digest(a) == request_digest(b)


# Excepción de canal por mod (decisión del dueño, 2026-10-09): "acepta beta para este mod", que
# solo cuenta mientras no haya versión en los canales del pack.


def test_a_beta_exception_takes_the_beta_when_no_release_fits() -> None:
    versions = [cand("P", "beta-1", type="beta", days=1), cand("P", "beta-2", type="beta", days=2)]
    lock = resolve(request("P", channel_exceptions={"P": "beta"}), catalog(*versions))
    assert lock.problems == [] and chosen(lock) == {"P": "beta-2"} and lock.excepted == ["P"]


def test_an_exception_has_no_effect_once_a_release_fits() -> None:
    versions = [cand("P", "rel-1", days=1), cand("P", "beta-9", type="beta", days=9)]
    lock = resolve(request("P", channel_exceptions={"P": "beta"}), catalog(*versions))
    assert chosen(lock) == {"P": "rel-1"} and lock.excepted == []


def test_a_beta_exception_does_not_accept_an_alpha() -> None:
    lock = resolve(request("P", channel_exceptions={"P": "beta"}), catalog(cand("P", "alpha-1", type="alpha")))
    [problem] = lock.problems
    assert problem.kind == "unresolvable" and problem.alternative is not None and lock.excepted == []


def test_an_alpha_exception_prefers_a_beta_over_a_newer_alpha() -> None:
    versions = [cand("P", "beta-1", type="beta", days=1), cand("P", "alpha-1", type="alpha", days=20)]
    lock = resolve(request("P", channel_exceptions={"P": "alpha"}), catalog(*versions))
    assert chosen(lock) == {"P": "beta-1"} and lock.excepted == ["P"]


def test_an_exception_reaches_a_dependency_too() -> None:
    versions = [cand("A", "a", deps=(("D", None, "required"),)), cand("D", "d-beta", type="beta")]
    lock = resolve(request("A", channel_exceptions={"D": "beta"}), catalog(*versions))
    assert lock.problems == [] and chosen(lock) == {"A": "a", "D": "d-beta"} and lock.excepted == ["D"]


def test_digest_without_exceptions_is_the_one_stored_before_they_existed() -> None:
    import hashlib
    import json as json_module

    plain = digest_request()
    data = plain.model_dump(mode="json")
    del data["channel_exceptions"]
    data["projects"] = sorted(data["projects"])
    data["target"]["channels"] = sorted(data["target"]["channels"])
    before = hashlib.sha256(json_module.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert request_digest(plain) == before
    assert request_digest(digest_request(channel_exceptions={"a": "beta"})) != before


# Datos de cada problema: quién lo pidió, la alternativa y el otro lado del conflicto.


def test_only_a_beta_fits_offers_it_as_the_alternative() -> None:
    versions = [cand("P", "beta-1", type="beta"), cand("P", "old", game="1.21.11")]
    [problem] = resolve(request("P"), catalog(*versions)).problems
    assert problem.kind == "unresolvable"
    assert problem.alternative is not None
    assert (problem.alternative.version_id, problem.alternative.channel) == ("beta-1", "beta")
    assert problem.alternative.version_number == "beta-1" and problem.alternative.published == EPOCH


def test_beta_wins_over_a_newer_alpha() -> None:
    versions = [cand("P", "beta-1", type="beta", days=1), cand("P", "alpha-1", type="alpha", days=20)]
    [problem] = resolve(request("P"), catalog(*versions)).problems
    assert problem.alternative is not None and problem.alternative.channel == "beta"


def test_no_fitting_version_has_no_alternative() -> None:
    [problem] = resolve(request("P"), catalog(cand("P", "old", game="1.21.11"))).problems
    assert problem.alternative is None and problem.pinned is None


def test_a_dependency_problem_names_who_asked() -> None:
    versions = [cand("A", "a", deps=(("D", None, "required"),)), cand("D", "d", game="1.21.11")]
    [problem] = resolve(request("A"), catalog(*versions)).problems
    assert problem.project_id == "D" and problem.requested_by == ["A"]


def test_a_dependency_without_project_nor_version_names_who_asked() -> None:
    # Without it, its card offers nothing: the only fix is removing the mod that asks for it.
    [problem] = resolve(request("A"), catalog(cand("A", "a", deps=((None, None, "required"),)))).problems
    assert problem.project_id is None and problem.requested_by == ["A"]


def test_a_pack_mod_problem_has_nobody_asking() -> None:
    [problem] = resolve(request("P"), catalog(cand("P", "old", game="1.21.11"))).problems
    assert problem.requested_by == []


def test_incompatible_names_both_sides() -> None:
    versions = [cand("A", "a", deps=(("B", None, "incompatible"),)), cand("B", "b")]
    [problem] = resolve(request("A", "B"), catalog(*versions)).problems
    assert (problem.project_id, problem.conflict_with) == ("B", "A")


def test_a_problem_saved_before_these_fields_still_parses() -> None:
    old = Problem.model_validate({"kind": "unresolvable", "project_id": "P", "detail": "x"})
    assert (old.alternative, old.requested_by, old.conflict_with) == (None, [], None)
