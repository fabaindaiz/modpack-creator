"""Los orígenes de una fijación (O2, la base mínima de O8; docs/research/2026-10-21-observaciones-ronda-3.md).

Una fijación guarda de dónde salió cada versión candidata (el pack importado, el admin, la
resolución, un servidor), si pasa la validación del pack y cuál se usa. Funciones puras: el
resolvedor y las reglas de `src/library/pins.py`, sin base ni red.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from src.library.pins import (ORIGIN_LABEL, OUTSIDE_TAGS, PinView, import_selects, pin_view, resolution_inputs,
                              server_origins, server_origins_dropped, version_problem)
from src.library.resolution import (Candidate, CandidateFile, Catalog, ResolveRequest, Target, request_digest,
                                    resolve)
from src.model.modpack import ModpackBundle, PinOrigin, PinRecord, PinSource

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
MOD = "MIXTRACE"


def cand(version: str, *, days: int = 0, type: str = "release", game: str = "1.20.1", loader: str = "forge",
         status: str = "listed", project: str = MOD) -> Candidate:
    return Candidate(project_id=project, version_id=version, version_number=version, version_type=type,
                     status=status, date_published=EPOCH + timedelta(days=days), game_versions=[game],
                     loaders=[loader], environment="client_and_server", dependencies=[],
                     file=CandidateFile(filename=f"{version}.jar", url=f"https://cdn.modrinth.com/{version}.jar",
                                        sha1=version, sha512=version * 2, size=1))


def request(**overrides: Any) -> ResolveRequest:
    fields: dict[str, Any] = {"target": Target(game_version="1.20.1", loader="forge"), "projects": [MOD]}
    fields.update(overrides)
    return ResolveRequest(**fields)


def origin(source: PinSource, version: str, valid: Optional[bool] = True, problem: Optional[str] = None) -> PinOrigin:
    return PinOrigin(source=source, version_id=version, version_number=version, valid=valid, problem=problem)


# El resolvedor.


def test_a_pack_version_outside_modrinths_tags_is_used_and_marked() -> None:
    # MixinTrace Reborn: la versión del pack no dice 1.20.1 en Modrinth, pero el pack la trae.
    off = cand("mt-pack", game="1.20")
    lock = resolve(request(pins={MOD: "mt-pack"}, unchecked={MOD}), Catalog.of([off]))
    assert not lock.problems
    [entry] = lock.entries
    assert (entry.version_id, entry.off_tags, entry.reason) == ("mt-pack", True, OUTSIDE_TAGS)


def test_an_admin_pin_outside_the_tags_is_still_the_pins_problem() -> None:
    lock = resolve(request(pins={MOD: "mt-pack"}), Catalog.of([cand("mt-pack", game="1.20")]))
    assert not lock.entries and [p.kind for p in lock.problems] == ["pin_mismatch"]


def test_a_pack_version_that_fits_is_not_marked() -> None:
    lock = resolve(request(pins={MOD: "mt-1"}, unchecked={MOD}), Catalog.of([cand("mt-1")]))
    assert [(e.version_id, e.off_tags) for e in lock.entries] == [("mt-1", False)]


def test_resolving_says_what_it_would_take_freely_for_a_mod_the_pack_pins() -> None:
    catalog = Catalog.of([cand("mt-1"), cand("mt-2", days=5)])
    lock = resolve(request(pins={MOD: "mt-1"}, unchecked={MOD}, free={MOD}), catalog)
    assert [e.version_id for e in lock.entries] == ["mt-1"]
    assert lock.free == {MOD: "mt-2"}


def test_the_free_choice_never_goes_below_the_floor() -> None:
    catalog = Catalog.of([cand("mt-1"), cand("mt-2", days=5), cand("mt-3", days=9, type="beta")])
    lock = resolve(request(pins={MOD: "mt-1"}, unchecked={MOD}, free={MOD}, floors={MOD: ["mt-2"]}), catalog)
    assert lock.free == {MOD: "mt-2"}


def test_a_free_choice_the_catalog_cannot_answer_yet_is_asked_for_without_blocking_the_lock() -> None:
    catalog = Catalog()
    catalog.add(cand("mt-pack", game="1.20"))
    catalog.types = {MOD: "mod"}
    # Only the pinned version is known: its list of versions has not been read.
    catalog.by_project.pop(MOD)
    lock = resolve(request(pins={MOD: "mt-pack"}, unchecked={MOD}, free={MOD}), catalog)
    assert lock.complete and [e.version_id for e in lock.entries] == ["mt-pack"]
    assert lock.free == {} and lock.free_needs == {MOD}


def test_a_request_without_origins_keeps_its_digest() -> None:
    plain = request(pins={MOD: "mt-1"})
    assert request_digest(plain) == request_digest(request(pins={MOD: "mt-1"}, unchecked=set(), free=set()))
    assert request_digest(plain) != request_digest(request(pins={MOD: "mt-1"}, unchecked={MOD}))


# La validación.


def test_validation_says_what_the_version_lacks() -> None:
    target = Target(game_version="1.20.1", loader="forge")
    assert version_problem(cand("ok"), target) is None
    assert version_problem(cand("x", game="1.20"), target) == "Modrinth no la marca para 1.20.1"
    assert version_problem(cand("x", loader="fabric"), target) == "Modrinth no la marca para forge"
    assert version_problem(cand("x", status="unlisted"), target) == "no está publicada en Modrinth"
    assert version_problem(cand("x", type="beta"), target) == "es beta, fuera de los canales del pack"
    assert version_problem(cand("x", type="beta"), target, exception="beta") is None


# La fijación vista con sus orígenes.


def bundle(pins: dict[str, str], records: Optional[dict[str, PinRecord]] = None) -> ModpackBundle:
    return ModpackBundle.model_construct(version="1.20.1", pins=pins, pin_origins=records or {})


def viewed(pack: ModpackBundle, project_id: str, resolved: Optional[str] = None) -> PinView:
    view = pin_view(pack, project_id, resolved=resolved)
    assert view is not None
    return view


def test_a_pin_stored_before_origins_reads_as_the_admins() -> None:
    view = viewed(bundle({MOD: "mt-1"}), MOD)
    assert [(o.source, o.version_id) for o in view.origins] == [(PinSource.admin, "mt-1")]
    assert view.selected == PinSource.admin


def test_a_pinned_pack_version_is_the_selected_origin() -> None:
    record = PinRecord(origins=[origin(PinSource.pack, "mt-pack", valid=False, problem="Modrinth no la marca para 1.20.1"),
                                origin(PinSource.resolution, "mt-2")], selected=PinSource.pack)
    view = viewed(bundle({MOD: "mt-pack"}, {MOD: record}), MOD)
    assert view.selected == PinSource.pack
    assert [o.source for o in view.origins] == [PinSource.pack, PinSource.resolution]


def test_a_pin_changed_elsewhere_reads_as_the_admins_and_keeps_the_other_origins() -> None:
    # Un PATCH de /v1 cambió el pin sin tocar los orígenes: la versión fijada manda.
    record = PinRecord(origins=[origin(PinSource.pack, "mt-pack")], selected=PinSource.pack)
    view = viewed(bundle({MOD: "mt-9"}, {MOD: record}), MOD)
    assert view.selected == PinSource.admin
    assert {(o.source, o.version_id) for o in view.origins} == {(PinSource.pack, "mt-pack"), (PinSource.admin, "mt-9")}


def test_without_a_pin_the_resolution_is_selected() -> None:
    record = PinRecord(origins=[origin(PinSource.pack, "mt-pack")], selected=PinSource.resolution)
    view = viewed(bundle({}, {MOD: record}), MOD, resolved="mt-2")
    assert view.selected == PinSource.resolution
    assert [(o.source, o.version_id) for o in view.origins] == [(PinSource.pack, "mt-pack"), (PinSource.resolution, "mt-2")]


def test_a_mod_with_no_pin_and_no_origins_has_no_view() -> None:
    assert pin_view(bundle({}), MOD) is None


def test_the_resolver_takes_unchecked_only_the_pack_or_server_versions_in_use() -> None:
    records = {MOD: PinRecord(origins=[origin(PinSource.pack, "mt-pack", valid=False)], selected=PinSource.pack),
               "ADMINPIN": PinRecord(origins=[origin(PinSource.pack, "a-1"), origin(PinSource.admin, "a-2")],
                                     selected=PinSource.admin),
               "FREEMOD": PinRecord(origins=[origin(PinSource.pack, "f-1")], selected=PinSource.resolution)}
    unchecked, free = resolution_inputs(bundle({MOD: "mt-pack", "ADMINPIN": "a-2", "LEGACY": "l-1"}, records))
    assert unchecked == {MOD}
    # What resolving freely takes is asked for every pinned mod that has another origin than the admin.
    assert free == {MOD, "ADMINPIN"}


# Importar: la versión del pack se usa salvo que pise una elección o baje el mod.


def test_importing_selects_the_pack_version_when_nothing_else_is_chosen() -> None:
    assert import_selects(current=None, downgrade=False) is True
    assert import_selects(current=PinRecord(origins=[origin(PinSource.pack, "old")], selected=PinSource.pack),
                          downgrade=False) is True


def test_importing_never_replaces_a_choice_nor_goes_back() -> None:
    chosen = PinRecord(origins=[origin(PinSource.admin, "a-1")], selected=PinSource.admin)
    assert import_selects(current=chosen, downgrade=False) is False
    freed = PinRecord(origins=[origin(PinSource.pack, "p-1")], selected=PinSource.resolution)
    assert import_selects(current=freed, downgrade=False) is False
    assert import_selects(current=None, downgrade=True) is False


def test_a_legacy_pin_is_a_choice_importing_keeps() -> None:
    assert import_selects(current=None, downgrade=False, pinned=True) is False


def test_every_origin_has_a_label() -> None:
    assert {s: ORIGIN_LABEL[s] for s in PinSource} == {
        PinSource.pack: "versión del pack", PinSource.admin: "elegida a mano", PinSource.resolution: "resolución",
        PinSource.server: "servidor"}



# Un servidor vinculado escaneado (decisión del dueño, 2026-10-21): la versión que tiene instalada un mod
# fijado, si difiere de la fijada, es su origen «servidor», validado, nunca en uso solo.

AT = datetime(2026, 10, 21, tzinfo=timezone.utc)
FORGE = Target(game_version="1.20.1", loader="forge")


def test_a_pinned_mod_the_server_runs_at_another_version_gets_a_server_origin_not_in_use() -> None:
    pack = bundle({MOD: "mt-1", "SAME": "s-1"},
                  {MOD: PinRecord(origins=[origin(PinSource.pack, "mt-1")], selected=PinSource.pack)})
    found = server_origins(pack, {MOD: "mt-2", "SAME": "s-1", "FREE": "f-9"},
                           {"mt-2": cand("mt-2", game="1.20")}, FORGE, "s1", "Survival", AT)
    # The mod on its pinned version and the mod with no pin get nothing.
    assert set(found) == {MOD}
    record = found[MOD]
    assert record.selected == PinSource.pack
    assert [(o.source, o.version_id, o.valid, o.problem, o.label, o.at) for o in record.origins] == [
        (PinSource.pack, "mt-1", True, None, None, None),
        (PinSource.server, "mt-2", False, "Modrinth no la marca para 1.20.1", "Survival", AT)]


def test_a_legacy_pin_keeps_the_admins_version_in_use_and_an_unknown_version_is_unchecked() -> None:
    found = server_origins(bundle({MOD: "mt-1"}), {MOD: "mt-2"}, {}, FORGE, "s1", "Survival", AT)
    record = found[MOD]
    assert record.selected == PinSource.admin
    assert [(o.source, o.version_id, o.valid) for o in record.origins] == [(PinSource.admin, "mt-1", None),
                                                                           (PinSource.server, "mt-2", None)]


def test_a_server_origin_is_updated_but_never_one_in_use_nor_rewritten_when_unchanged() -> None:
    known = PinRecord(origins=[origin(PinSource.admin, "mt-1"),
                               PinOrigin(source=PinSource.server, version_id="mt-2", label="Survival", server_id="s1")],
                      selected=PinSource.admin)
    pack = bundle({MOD: "mt-1"}, {MOD: known})
    assert server_origins(pack, {MOD: "mt-2"}, {}, FORGE, "s1", "Survival", AT) == {}
    moved = server_origins(pack, {MOD: "mt-3"}, {"mt-3": cand("mt-3")}, FORGE, "s1", "Survival", AT)[MOD]
    assert [(o.source, o.version_id, o.label, o.server_id) for o in moved.origins] == [
        (PinSource.admin, "mt-1", None, None), (PinSource.server, "mt-3", "Survival", "s1")]
    in_use = PinRecord(origins=[PinOrigin(source=PinSource.server, version_id="mt-1", label="Survival")],
                       selected=PinSource.server)
    assert server_origins(bundle({MOD: "mt-1"}, {MOD: in_use}), {MOD: "mt-2"}, {}, FORGE, "s1", "Survival", AT) == {}


# Uno por servidor (dueño, 2026-10-22): cada servidor vinculado que difiere tiene su línea, y la pierde
# cuando vuelve a la versión fijada, deja de tener el mod o se desvincula.

def served(server_id: str, name: str, version: str) -> PinOrigin:
    return PinOrigin(source=PinSource.server, version_id=version, label=name, server_id=server_id)


def test_each_linked_server_keeps_its_own_origin() -> None:
    known = PinRecord(origins=[origin(PinSource.admin, "mt-1"), served("s1", "Survival", "mt-2")],
                      selected=PinSource.admin)
    found = server_origins(bundle({MOD: "mt-1"}, {MOD: known}), {MOD: "mt-3"}, {}, FORGE, "s2", "Creativo", AT)[MOD]
    assert [(o.source, o.server_id, o.version_id) for o in found.origins] == [
        (PinSource.admin, None, "mt-1"), (PinSource.server, "s1", "mt-2"), (PinSource.server, "s2", "mt-3")]
    assert found.selected == PinSource.admin


def test_a_servers_origin_goes_when_it_runs_the_pinned_version_again_or_no_longer_has_the_mod() -> None:
    known = PinRecord(origins=[origin(PinSource.admin, "mt-1"), served("s1", "Survival", "mt-2"),
                               served("s2", "Creativo", "mt-3")], selected=PinSource.admin)
    pack = bundle({MOD: "mt-1"}, {MOD: known})
    for installed in ({MOD: "mt-1"}, {}):
        found = server_origins(pack, installed, {}, FORGE, "s1", "Survival", AT)[MOD]
        assert [(o.source, o.server_id) for o in found.origins] == [(PinSource.admin, None), (PinSource.server, "s2")]


def test_the_servers_origin_in_use_is_left_while_another_server_still_adds_its_own() -> None:
    in_use = PinRecord(origins=[served("s1", "Survival", "mt-1"), served("s2", "Creativo", "mt-4")],
                       selected=PinSource.server, selected_key="server:s1")
    pack = bundle({MOD: "mt-1"}, {MOD: in_use})
    assert server_origins(pack, {MOD: "mt-3"}, {}, FORGE, "s1", "Survival", AT) == {}
    found = server_origins(pack, {MOD: "mt-5"}, {}, FORGE, "s2", "Creativo", AT)[MOD]
    assert [(o.server_id, o.version_id) for o in found.origins] == [("s1", "mt-1"), ("s2", "mt-5")]
    assert (found.selected, found.selected_key) == (PinSource.server, "server:s1")


def test_a_server_origin_stored_without_its_id_is_that_servers_by_name() -> None:
    # Escrito antes de guardar el id del servidor (2026-10-21): se reconoce por el nombre y lo toma.
    known = PinRecord(origins=[origin(PinSource.admin, "mt-1"),
                               PinOrigin(source=PinSource.server, version_id="mt-2", label="Survival")],
                      selected=PinSource.admin)
    found = server_origins(bundle({MOD: "mt-1"}, {MOD: known}), {MOD: "mt-2"}, {}, FORGE, "s1", "Survival", AT)[MOD]
    assert [(o.source, o.server_id, o.version_id) for o in found.origins] == [
        (PinSource.admin, None, "mt-1"), (PinSource.server, "s1", "mt-2")]


def test_the_card_shows_which_server_origin_is_in_use() -> None:
    record = PinRecord(origins=[origin(PinSource.admin, "mt-1"), served("s1", "Survival", "mt-2"),
                                served("s2", "Creativo", "mt-3")], selected=PinSource.server, selected_key="server:s2")
    view = viewed(bundle({MOD: "mt-3"}, {MOD: record}), MOD)
    assert (view.selected, view.selected_key) == (PinSource.server, "server:s2")
    assert [o.key for o in view.origins] == ["admin", "server:s1", "server:s2"]


def test_unlinking_a_server_drops_its_origins_except_the_one_in_use() -> None:
    offered = PinRecord(origins=[origin(PinSource.admin, "mt-1"), served("s1", "Survival", "mt-2")],
                        selected=PinSource.admin)
    used = PinRecord(origins=[served("s1", "Survival", "o-1")], selected=PinSource.server, selected_key="server:s1")
    pack = bundle({MOD: "mt-1", "OTHER": "o-1"}, {MOD: offered, "OTHER": used})
    dropped = server_origins_dropped(pack, "s1", "Survival")
    assert set(dropped) == {MOD}
    assert [o.source for o in dropped[MOD].origins] == [PinSource.admin]
