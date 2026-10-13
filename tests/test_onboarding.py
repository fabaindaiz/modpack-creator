"""The guided set-up (batch B, B1; d-7985ee-20935c): from what is known of a server or a pack, the
steps to a tested build, each done or with its action, and a step whose previous one is not done
says what it waits for instead of offering its action."""
from src.library.onboarding import PackFacts, ServerFacts, pack_steps, server_steps

NEW = ServerFacts(scanned=False, identified=0, bundle_id=None, bundle_name=None, resolved=False, pending=0,
                  latest_build=None, simulated_build=None)


def keys_done(steps: list) -> list[tuple[str, bool]]:
    return [(s.key, s.done) for s in steps]


def test_a_new_server_has_every_step_left_and_only_the_first_offers_its_action() -> None:
    steps = server_steps("s1", NEW)
    assert keys_done(steps) == [("scan", False), ("pack", False), ("resolve", False), ("build", False),
                                ("simulate", False)]
    assert steps[0].action == ("Escanear", "/servers/s1#escaneo")
    assert steps[1].action == ("Crear o vincular", "/servers/s1#modpack")  # scanning is not required to link
    assert steps[2].action is None and steps[2].waits_for == "Pack vinculado"
    assert steps[3].waits_for == "Pack resuelto" and steps[4].waits_for == "Build creado"


def test_a_linked_server_with_pending_mods_points_to_deciding_them() -> None:
    facts = NEW._replace(scanned=True, identified=120, bundle_id="b1", bundle_name="Survival", resolved=True, pending=2)
    steps = server_steps("s1", facts)
    assert keys_done(steps)[:3] == [("scan", True), ("pack", True), ("resolve", False)]
    assert steps[0].detail == "120 mods identificados" and steps[1].detail == "Survival"
    assert steps[2].detail == "2 por decidir o con problemas"
    assert steps[2].action == ("Decidir", "/modpacks/b1?vista=decidir")


def test_an_unresolved_pack_offers_resolving() -> None:
    facts = NEW._replace(scanned=True, bundle_id="b1", bundle_name="Survival")
    assert server_steps("s1", facts)[2].action == ("Resolver", "/modpacks/b1#mods")


def test_a_build_not_simulated_yet_offers_the_simulation() -> None:
    facts = NEW._replace(scanned=True, bundle_id="b1", resolved=True, latest_build=3, simulated_build=2)
    steps = server_steps("s1", facts)
    assert keys_done(steps)[3:] == [("build", True), ("simulate", False)]
    assert steps[3].detail == "Build 3" and steps[4].action == ("Simular", "/servers/s1#actualizar")


def test_a_server_whose_last_build_was_simulated_is_set_up() -> None:
    facts = NEW._replace(scanned=True, bundle_id="b1", resolved=True, latest_build=3, simulated_build=3)
    assert all(s.done for s in server_steps("s1", facts))


def test_every_step_says_why_it_matters() -> None:
    assert all(s.why for s in server_steps("s1", NEW))
    assert all(s.why for s in pack_steps("b1", PackFacts(projects=0, resolved=False, pending=0, latest_build=None,
                                                         linked_servers=(), simulated=False)))


def test_a_pack_s_steps_end_on_a_linked_server_s_simulation() -> None:
    empty = PackFacts(projects=0, resolved=False, pending=0, latest_build=None, linked_servers=(), simulated=False)
    steps = pack_steps("b1", empty)
    assert keys_done(steps) == [("mods", False), ("resolve", False), ("build", False), ("server", False),
                                ("simulate", False)]
    assert steps[0].action == ("Buscar mods", "/modpacks/b1#mods")
    built = empty._replace(projects=4, resolved=True, latest_build=2, linked_servers=(("s1", "Survival"),))
    steps = pack_steps("b1", built)
    assert steps[3].done and steps[3].detail == "Survival"
    assert steps[4].action == ("Simular", "/servers/s1#actualizar")
    assert pack_steps("b1", built._replace(simulated=True))[4].done
    unlinked = pack_steps("b1", built._replace(linked_servers=()))
    assert unlinked[3].action == ("Vincular un servidor", "/servers") and unlinked[4].waits_for == "Servidor vinculado"


def test_linking_never_waits_and_simulating_waits_for_the_build_too() -> None:
    linked = PackFacts(projects=4, resolved=False, pending=0, latest_build=None, linked_servers=(("s1", "Survival"),),
                       simulated=False)
    steps = pack_steps("b1", linked)
    assert steps[3].done and steps[4].waits_for == "Build creado" and steps[4].action is None
    unlinked = pack_steps("b1", linked._replace(linked_servers=()))
    assert unlinked[3].action == ("Vincular un servidor", "/servers") and unlinked[3].waits_for is None


def test_resolving_points_to_the_mods_where_the_resolve_button_is() -> None:
    """User walk 2026-10-13 (K1): on the pack page the step pointed to the page itself, at the top."""
    facts = PackFacts(projects=2, resolved=False, pending=0, latest_build=None, linked_servers=(), simulated=False)
    assert pack_steps("b1", facts)[1].action == ("Resolver", "/modpacks/b1#mods")
    assert server_steps("s1", NEW._replace(bundle_id="b1"))[2].action == ("Resolver", "/modpacks/b1#mods")


def test_an_editor_is_told_an_operator_does_the_operator_steps() -> None:
    """User walk 2026-10-13 (K2): linking a pack and simulating are an operator's; an editor got a
    button leading to a disabled control."""
    steps = server_steps("s1", NEW._replace(scanned=True, bundle_id="b1", resolved=True, latest_build=2), operator=False)
    linked = server_steps("s1", NEW, operator=False)[1]
    assert linked.action is None and linked.note == "Lo hace un operador."
    assert steps[4].action is None and steps[4].note == "Lo hace un operador."
    pack = pack_steps("b1", PackFacts(projects=2, resolved=True, pending=0, latest_build=2,
                                      linked_servers=(("s1", "Survival"),), simulated=False), operator=False)
    assert pack[3].done and pack[4].action is None and pack[4].note == "Lo hace un operador."
    assert server_steps("s1", NEW)[1].note is None  # an operator gets the action
