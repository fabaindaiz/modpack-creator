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
    assert steps[3].waits_for == "Nada por decidir" and steps[4].waits_for == "Build creado"


def test_a_linked_server_with_pending_mods_points_to_deciding_them() -> None:
    facts = NEW._replace(scanned=True, identified=120, bundle_id="b1", bundle_name="Survival", resolved=True, pending=2)
    steps = server_steps("s1", facts)
    assert keys_done(steps)[:3] == [("scan", True), ("pack", True), ("resolve", False)]
    assert steps[0].detail == "120 mods identificados" and steps[1].detail == "Survival"
    assert steps[2].detail == "2 por decidir o con problemas"
    assert steps[2].action == ("Decidir", "/modpacks/b1?vista=decidir")


def test_an_unresolved_pack_leads_to_the_pack_which_resolves_by_itself() -> None:
    """O9 D2 (owner, 2026-10-26): no «Resolver» step; opening the pack resolves it."""
    facts = NEW._replace(scanned=True, bundle_id="b1", bundle_name="Survival")
    step = server_steps("s1", facts)[2]
    assert step.label == "Nada por decidir" and step.action == ("Abrir el pack", "/modpacks/b1#mods-section")


def test_a_build_not_simulated_yet_offers_the_simulation() -> None:
    facts = NEW._replace(scanned=True, bundle_id="b1", resolved=True, latest_build=3, simulated_build=2)
    steps = server_steps("s1", facts)
    assert keys_done(steps)[3:] == [("build", True), ("simulate", False)]
    assert steps[3].detail == "Build 3" and steps[4].action == ("Simular instalación del build 3", "/servers/s1?build=3#actualizar")


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
    assert steps[0].action == ("Buscar mods", "/modpacks/b1#mods-section")
    built = empty._replace(projects=4, resolved=True, latest_build=2, linked_servers=(("s1", "Survival"),))
    steps = pack_steps("b1", built)
    assert steps[3].done and steps[3].detail == "Survival"
    assert steps[4].action == ("Simular instalación del build 2", "/servers/s1?build=2#actualizar")
    assert pack_steps("b1", built._replace(simulated=True))[4].done
    unlinked = pack_steps("b1", built._replace(linked_servers=()))
    assert unlinked[3].action == ("Vincular un servidor", "/modpacks/b1#servers-section") and unlinked[4].waits_for == "Servidor vinculado"


def test_linking_never_waits_and_simulating_waits_for_the_build_too() -> None:
    linked = PackFacts(projects=4, resolved=False, pending=0, latest_build=None, linked_servers=(("s1", "Survival"),),
                       simulated=False)
    steps = pack_steps("b1", linked)
    assert steps[3].done and steps[4].waits_for == "Build creado" and steps[4].action is None
    unlinked = pack_steps("b1", linked._replace(linked_servers=()))
    assert unlinked[3].action == ("Vincular un servidor", "/modpacks/b1#servers-section") and unlinked[3].waits_for is None


def test_no_step_offers_resolving() -> None:
    """O9 D2 (owner, 2026-10-26): every action resolves the pack; the guide keeps «Decidir»."""
    facts = PackFacts(projects=2, resolved=False, pending=0, latest_build=None, linked_servers=(), simulated=False)
    steps = pack_steps("b1", facts) + server_steps("s1", NEW._replace(bundle_id="b1"))
    assert all(s.action is None or s.action[0] != "Resolver" for s in steps)
    assert "Resolver" not in " ".join(s.why for s in steps)


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


def test_a_resolved_pack_with_problems_points_to_its_problems_first() -> None:
    """As the dashboard does (O9 user-walk, A4): with only problems, «Decidir» on the undecided tab
    reads «No quedan mods por decidir»."""
    facts = NEW._replace(scanned=True, bundle_id="b1", resolved=True, pending=2, problems=1)
    assert server_steps("s1", facts)[2].action == ("Decidir", "/modpacks/b1?vista=problemas")
    pack = PackFacts(projects=3, resolved=True, pending=1, latest_build=None, linked_servers=(), simulated=False,
                     problems=1)
    assert pack_steps("b1", pack)[1].action == ("Decidir", "/modpacks/b1?vista=problemas")


def test_searching_for_mods_leads_to_the_search_field() -> None:
    """The step lands on the search, not below it (O9 user-walk, A5)."""
    pack = PackFacts(projects=0, resolved=False, pending=0, latest_build=None, linked_servers=(), simulated=False)
    assert pack_steps("b1", pack)[0].action == ("Buscar mods", "/modpacks/b1#mods-section")


def test_on_the_pack_itself_an_unresolved_pack_waits_instead_of_linking_to_itself() -> None:
    """The visual sweep (2026-10-26): «Abrir el pack» on the pack's own page led nowhere; there it
    waits for what Modrinth still has to bring."""
    facts = PackFacts(projects=2, resolved=False, pending=0, latest_build=None, linked_servers=(), simulated=False)
    step = pack_steps("b1", facts)[1]
    assert step.action is None and step.detail == "se resuelve al traer de Modrinth lo que falta"
