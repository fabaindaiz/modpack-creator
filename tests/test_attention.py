"""La franja «Necesita atención» del tablero (B2, d-7985ee-c96727): una función pura de los hechos
que el tablero ya lee a una lista de tarjetas de una acción, por gravedad y luego la más nueva."""
from datetime import datetime, timedelta, timezone

from beanie import PydanticObjectId

from src.library.attention import VISIBLE, Attention, FailedJob, PackFact, ServerFact, attention, failed_job_fact, unresolved
from src.model.jobs import Job

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


def srv(name: str = "Survival", state: str = "sync", has_pack: bool = True,
        scanned_at: datetime | None = NOW, pack_deleted: bool = False) -> ServerFact:
    return ServerFact(id=f"id-{name}", name=name, state=state, has_pack=has_pack, scanned_at=scanned_at,
                      pack_deleted=pack_deleted)


def pack(name: str = "Pack Uno", **facts: object) -> PackFact:
    return PackFact(id=f"id-{name}", name=name, **facts)  # type: ignore[arg-type]


def test_nothing_to_attend_is_an_empty_list() -> None:
    assert attention([srv()], [pack()], [], NOW) == []


def test_a_drifted_server_asks_to_simulate_on_its_page() -> None:
    [item] = attention([srv(state="drift")], [], [], NOW)
    assert item.action_label == "Simular instalación"
    assert item.href == "/servers/id-Survival#actualizar"
    assert "Survival" in item.text and item.at == NOW


def test_a_failed_job_of_the_last_day_asks_to_see_it() -> None:
    job = FailedJob(id="j1", kind="server_update", about="Survival", at=NOW - timedelta(hours=3))
    [item] = attention([], [], [job], NOW)
    assert (item.action_label, item.href) == ("Ver", "/jobs/j1")
    assert item.text == "Falló la actualización de Survival."


def test_a_failed_job_older_than_a_day_is_not_shown() -> None:
    job = FailedJob(id="j1", kind="server_scan", about=None, at=NOW - timedelta(hours=25))
    assert attention([], [], [job], NOW) == []


def test_a_failed_job_without_a_known_object_still_says_what_failed() -> None:
    job = FailedJob(id="j1", kind="server_scan", about=None, at=NOW)
    assert attention([], [], [job], NOW)[0].text == "Falló el escaneo."
    unknown = FailedJob(id="j2", kind="otra_cosa", about=None, at=NOW)
    assert attention([], [], [unknown], NOW)[0].text == "Falló un trabajo."


def test_a_pack_with_undecided_mods_asks_to_decide() -> None:
    [item] = attention([], [pack(undecided=2)], [], NOW)
    assert item.action_label == "Decidir"
    assert item.href == "/modpacks/id-Pack Uno?vista=decidir"
    assert item.text == "Pack Uno tiene 2 mods por decidir."


def test_a_pack_with_problems_goes_to_its_problems() -> None:
    [item] = attention([], [pack(problems=1, undecided=1)], [], NOW)
    assert item.action_label == "Arreglar"  # O9 D12 (owner, 2026-10-26)
    assert item.href == "/modpacks/id-Pack Uno?vista=problemas"
    assert item.text == "Pack Uno tiene 1 problema y 1 mod por decidir."


def test_a_pack_with_updates_asks_to_see_them() -> None:
    [item] = attention([], [pack(updates=3, checked_at=NOW)], [], NOW)
    assert (item.action_label, item.href) == ("Ver actualizaciones", "/modpacks/id-Pack Uno/actualizaciones")
    assert item.text == "Pack Uno tiene 3 actualizaciones disponibles."
    assert item.at == NOW


def test_a_server_never_scanned_asks_to_scan() -> None:
    [item] = attention([srv(state="none", scanned_at=None)], [], [], NOW)
    assert (item.action_label, item.href) == ("Escanear", "/servers/id-Survival#escaneo")
    assert item.text == "Survival nunca se ha escaneado."


def test_a_server_without_a_pack_asks_to_link_one() -> None:
    [item] = attention([srv(has_pack=False, state="unknown")], [], [], NOW)
    assert (item.action_label, item.href) == ("Vincular", "/servers/id-Survival#modpack")
    assert item.text == "Survival no tiene un modpack vinculado."


def test_order_is_by_severity_then_newest_first() -> None:
    items = attention(
        [srv("Viejo", state="drift", scanned_at=NOW - timedelta(days=2)),
         srv("Nuevo", state="drift", scanned_at=NOW - timedelta(hours=1)),
         srv("Suelto", has_pack=False, state="unknown")],
        [pack(updates=1, checked_at=NOW)],
        [FailedJob(id="j1", kind="build_create", about="Pack Uno", at=NOW - timedelta(hours=20))],
        NOW)
    assert [i.action_label for i in items] == ["Ver", "Simular instalación", "Simular instalación", "Ver actualizaciones", "Vincular"]
    assert "Nuevo" in items[1].text and "Viejo" in items[2].text
    assert items == sorted(items, key=lambda i: i.severity)


def test_an_item_without_a_date_goes_after_the_dated_ones_of_its_severity() -> None:
    items = attention([srv("Sin fecha", state="none", scanned_at=None)],
                      [pack(updates=1, checked_at=NOW - timedelta(days=9))], [], NOW)
    assert [i.action_label for i in items] == ["Ver actualizaciones", "Escanear"]


def test_naive_dates_from_mongodb_are_read_as_utc() -> None:
    job = FailedJob(id="j1", kind="server_scan", about=None, at=(NOW - timedelta(hours=1)).replace(tzinfo=None))
    assert len(attention([], [], [job], NOW)) == 1


def test_the_dashboard_shows_five() -> None:
    assert VISIBLE == 5
    items = attention([srv(f"S{n}", has_pack=False) for n in range(7)], [], [], NOW)
    assert len(items) == 7 and all(isinstance(i, Attention) for i in items)


def test_failed_job_fact_names_its_server_or_its_pack() -> None:
    server_id, bundle_id = str(PydanticObjectId()), str(PydanticObjectId())
    on_server = Job.model_construct(id=PydanticObjectId(), kind="server_scan", key=f"server:{server_id}",
                                    payload={}, status="failed", created_at=NOW, finished_at=None)
    on_pack = Job.model_construct(id=PydanticObjectId(), kind="build_create", key=None,
                                  payload={"bundle_id": bundle_id}, status="failed",
                                  created_at=NOW - timedelta(hours=1), finished_at=NOW)
    fact = failed_job_fact(on_server, {server_id: "Survival"}, {})
    assert fact == FailedJob(id=str(on_server.id), kind="server_scan", about="Survival", at=NOW)
    assert failed_job_fact(on_pack, {}, {bundle_id: "Pack Uno"}).about == "Pack Uno"
    assert failed_job_fact(on_pack, {}, {}).about is None


def test_a_failed_simulation_says_the_server_did_not_change() -> None:
    """Group 2 (owner, 2026-10-21): a dry run that failed wrote nothing, and the card says so; a real
    update that failed keeps its text."""
    simulated = FailedJob(id="j1", kind="server_update", about="Survival", at=NOW, simulation=True)
    real = FailedJob(id="j2", kind="server_update", about="Lobby", at=NOW)
    texts = [i.text for i in attention([], [], [simulated, real], NOW)]
    assert "Falló la simulación de Survival: el servidor no cambió." in texts
    assert "Falló la actualización de Lobby." in texts


def test_failed_job_fact_tells_a_simulation_from_a_real_update() -> None:
    """A dry run is a `server_update` whose payload does not say `dry_run: False` (`is_real_update`)."""
    def update(payload: dict[str, object]) -> Job:
        return Job.model_construct(id=PydanticObjectId(), kind="server_update", key="server:s1", payload=payload,
                                   status="failed", created_at=NOW, finished_at=NOW)
    assert failed_job_fact(update({"dry_run": True}), {}, {}).simulation
    assert failed_job_fact(update({}), {}, {}).simulation
    assert not failed_job_fact(update({"dry_run": False}), {}, {}).simulation
    scan = Job.model_construct(id=PydanticObjectId(), kind="server_scan", key="server:s1", payload={},
                               status="failed", created_at=NOW, finished_at=NOW)
    assert not failed_job_fact(scan, {}, {}).simulation


def test_a_server_whose_pack_was_deleted_asks_to_link_another() -> None:
    """Group 2 (owner, 2026-10-21): the server still points to a pack that no longer exists. Same
    action and level as a server that never had one, which keeps its text."""
    [item] = attention([srv(has_pack=False, pack_deleted=True, state="unknown")], [], [], NOW)
    assert item.text == "El modpack de Survival se borró: vincula otro."
    assert (item.action_label, item.href, item.severity) == ("Vincular", "/servers/id-Survival#modpack", 3)
    [seen] = attention([srv(has_pack=False, pack_deleted=True, state="unknown")], [], [], NOW, operator=False)
    assert (seen.action_label, seen.href) == ("Ver", "/servers/id-Survival")
    [never] = attention([srv(has_pack=False, state="unknown")], [], [], NOW)
    assert never.text == "Survival no tiene un modpack vinculado."


def made(kind: str, status: str, key: str, minutes: int) -> Job:
    at = NOW - timedelta(minutes=minutes)
    return Job.model_construct(id=PydanticObjectId(), kind=kind, key=key, payload={}, status=status, created_at=at,
                               finished_at=at)


def test_a_failure_a_later_success_of_the_same_job_fixed_is_not_shown() -> None:
    """Final review of batch B (and the research's rule): a scan that failed on a blip and worked next
    time is no longer news. Another server's success or an older one fixes nothing."""
    failed = made("server_scan", "failed", "server:s1", 30)
    assert unresolved([failed], [made("server_scan", "done", "server:s1", 10)]) == []
    assert unresolved([failed], [made("server_scan", "done", "server:s2", 10)]) == [failed]
    assert unresolved([failed], [made("server_scan", "done", "server:s1", 40)]) == [failed]
    assert unresolved([failed], [made("server_update", "done", "server:s1", 10)]) == [failed]


def test_scanning_lands_on_the_scan_section() -> None:
    """User walk 2026-10-13 (K3): the card led to the top of the server page, the scan button far below."""
    [item] = attention([srv(state="none", scanned_at=None)], [], [], NOW)
    assert item.href.endswith("#escaneo")


def test_an_editor_sees_operator_cards_without_an_action_of_theirs() -> None:
    """User walk 2026-10-13 (K2): simulating and linking are an operator's (d-7985ee-edef1e). An editor
    sees the card, and opens the page to look, not to act."""
    items = attention([srv(state="drift"), srv("Lobby", has_pack=False, state="unknown")], [], [], NOW, operator=False)
    assert {(i.action_label, i.href) for i in items} == {("Ver", "/servers/id-Survival"), ("Ver", "/servers/id-Lobby")}


def test_bridged_mods_whose_native_version_caught_up_are_suggested_never_moved() -> None:
    """Batch C (C7, d-7985ee-7e602f): «Pasar a nativa» is offered in the «Vía puente» tab; the card leads there."""
    [item] = attention([], [pack(native_ready=2, resolved_at=NOW)], [], NOW)
    assert item.text == "Pack Uno: 2 mods ya tienen versión nativa."
    assert (item.action_label, item.href) == ("Ver", "/modpacks/id-Pack Uno?vista=puente")
    assert item.severity == 2 and item.at == NOW
    [one] = attention([], [pack(native_ready=1)], [], NOW)
    assert one.text == "Pack Uno: 1 mod ya tiene versión nativa."
