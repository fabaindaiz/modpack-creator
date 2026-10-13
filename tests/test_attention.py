"""La franja «Necesita atención» del tablero (B2, d-7985ee-c96727): una función pura de los hechos
que el tablero ya lee a una lista de tarjetas de una acción, por gravedad y luego la más nueva."""
from datetime import datetime, timedelta, timezone

from beanie import PydanticObjectId

from src.library.attention import VISIBLE, Attention, FailedJob, PackFact, ServerFact, attention, failed_job_fact, unresolved
from src.model.jobs import Job

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


def srv(name: str = "Survival", state: str = "sync", has_pack: bool = True,
        scanned_at: datetime | None = NOW) -> ServerFact:
    return ServerFact(id=f"id-{name}", name=name, state=state, has_pack=has_pack, scanned_at=scanned_at)


def pack(name: str = "Pack Uno", **facts: object) -> PackFact:
    return PackFact(id=f"id-{name}", name=name, **facts)  # type: ignore[arg-type]


def test_nothing_to_attend_is_an_empty_list() -> None:
    assert attention([srv()], [pack()], [], NOW) == []


def test_a_drifted_server_asks_to_simulate_on_its_page() -> None:
    [item] = attention([srv(state="drift")], [], [], NOW)
    assert item.action_label == "Simular"
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
    assert item.action_label == "Decidir"
    assert item.href == "/modpacks/id-Pack Uno?vista=problemas"
    assert item.text == "Pack Uno tiene 1 problema y 1 mod por decidir."


def test_a_pack_with_updates_asks_to_see_them() -> None:
    [item] = attention([], [pack(updates=3, checked_at=NOW)], [], NOW)
    assert (item.action_label, item.href) == ("Ver actualizaciones", "/modpacks/id-Pack Uno/actualizaciones")
    assert item.text == "Pack Uno tiene 3 actualizaciones disponibles."
    assert item.at == NOW


def test_a_server_never_scanned_asks_to_scan() -> None:
    [item] = attention([srv(state="none", scanned_at=None)], [], [], NOW)
    assert (item.action_label, item.href) == ("Escanear", "/servers/id-Survival")
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
    assert [i.action_label for i in items] == ["Ver", "Simular", "Simular", "Ver actualizaciones", "Vincular"]
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
