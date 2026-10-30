"""The knowledge base in the panel (O8 tanda 3: D6, D7 of d-7985ee-a99db7; K-Q1, K-Q4, K-Q5 of d-7985ee-57d185;
user-walk K2, K10, K17).

The comparison of two builds shows what the base knows of each mod that changed, suspicions included, in its own
markup (never a resolver's problem). «Marcar como sospechoso» opens a dialog: the version that arrived is offered
a click away and never chosen for the editor; the other mod comes from the build that failed, or none (a version
broken alone). A pair already in the base is named, with «Sumarse a la existente».
"""
from typing import Any, Optional

from src.exceptions import HTTP_409_CONFLICT
from src.library.knowledge import KnownWarning
from src.model.users import Actor
from src.services.knowledge import IssueExists, SideInput
from tests.test_web_modpacks import BUNDLE_ID, HTMX, build, entry, lock, preview, setup_all


def warning(status: str = "confirmed", broken: bool = False) -> KnownWarning:
    return KnownWarning(issue_id="i1", status=status, summary="pantalla negra", project_ids=["P1"] if broken else ["P1", "P2"],
                        text="Versión rota: Iris 1.7.2" if broken else "Iris 1.7+ ✕ Sodium", broken=broken)


class Knowledge:
    """KnowledgeService for the panel: what it was asked, and what it answers."""

    def __init__(self) -> None:
        self.marked: list[tuple[Any, ...]] = []
        self.joined: list[tuple[Any, ...]] = []
        self.exists: Optional[str] = None

    async def issue_mark(self, bundle_id: str, a: SideInput, b: Optional[SideInput], game_version: Optional[str],
                         loader: Optional[str], summary: str, note_ids: list[str], key: str, actor: Actor) -> Any:
        if self.exists:
            raise IssueExists(self.exists)
        self.marked.append((bundle_id, a, b, game_version, loader, summary, note_ids, key))
        return type("Issue", (), {"id": "i9", "summary": summary})()

    async def issue_join(self, issue_id: str, bundle_id: str, note_ids: list[str], actor: Actor) -> Any:
        self.joined.append((issue_id, bundle_id, note_ids))
        return type("Issue", (), {"id": issue_id, "summary": "pantalla negra"})()

    async def get(self, issue_id: str) -> Any:
        return type("Issue", (), {"id": issue_id, "summary": "pantalla negra", "status": "confirmed"})()

    async def suspected_count(self) -> int:
        return 0


def knowledge_setup() -> tuple[Any, Knowledge, Any]:
    import main
    from dependency_injector import providers
    client, modpack, _, _, _ = setup_all()
    knowledge = Knowledge()
    main.container.knowledgeService.override(providers.Object(knowledge))
    two = build(2, lock=lock(entry("P1"), entry("P2"), entry("P3")))
    modpack.builds = [two, build(1)]
    return client, knowledge, modpack


def compare(modpack: Any, known: dict[str, list[KnownWarning]]) -> None:
    async def compare_builds(bundle_id: str, de: int, a: int) -> Any:
        return preview().model_copy(update={"known": known})

    modpack.compare_builds = compare_builds


def test_the_comparison_shows_known_and_suspected() -> None:
    client, _, modpack = knowledge_setup()
    compare(modpack, {"P1": [warning(), warning("suspected")]})
    text = client.get(f"/modpacks/{BUNDLE_ID}/builds/compare", params={"de": 1, "a": 2}).text
    assert "⚠ Conocida" in text and "? Sospecha" in text and "Iris 1.7+ ✕ Sodium" in text
    assert f'hx-get="/modpacks/{BUNDLE_ID}/conocimiento/marcar?de=1&amp;a=2&amp;mod=P1"' in text


def test_a_known_warning_is_not_drawn_as_a_problem() -> None:
    client, _, modpack = knowledge_setup()
    compare(modpack, {"P1": [warning()]})
    text = client.get(f"/modpacks/{BUNDLE_ID}/builds/compare", params={"de": 1, "a": 2}).text
    assert "data-known" in text and "data-problem" not in text


def dialog(client: Any) -> str:
    return str(client.get(f"/modpacks/{BUNDLE_ID}/conocimiento/marcar", params={"de": 1, "a": 2, "mod": "P1"}, headers=HTMX).text)


def test_the_mark_dialog_suggests_and_never_fills() -> None:
    client, _, _ = knowledge_setup()
    text = dialog(client)
    assert "<dialog" in text and "Marcar como sospechoso" in text
    # «cualquier versión» is chosen; the version that arrived in build 2 is a click away, never chosen.
    assert 'name="a_desde" value="" checked' in text
    assert 'name="a_desde" value="build">' in text and "la que trae el build 2 (1.0)" in text
    assert 'name="mc" value="1.21.1">' in text and 'name="loader" value="fabric">' in text
    assert 'data-draft="' in text and 'name="key"' in text


def test_the_second_mod_comes_from_the_failing_build() -> None:
    client, _, _ = knowledge_setup()
    text = dialog(client)
    select = text.split('name="b"')[1].split("</select>")[0]
    assert '<option value="">Ninguno: falla sola</option>' in select
    assert 'value="P2"' in select and 'value="P3"' in select and 'value="P1"' not in select


def test_marking_sends_the_versions_of_the_failing_build() -> None:
    client, knowledge, _ = knowledge_setup()
    response = client.post(f"/modpacks/{BUNDLE_ID}/conocimiento", headers=HTMX, data={
        "de": "1", "a": "2", "mod": "P1", "a_desde": "build", "b": "P2", "b_desde": "", "mc": "1.21.1",
        "summary": "pantalla negra", "notes": ["n1", "n2"], "key": "k1"})
    assert response.status_code == 200 and "Marcada como sospecha" in response.text
    [(bundle_id, a, b, mc, loader, summary, notes, key)] = knowledge.marked
    assert (bundle_id, a, b, mc, loader) == (str(BUNDLE_ID), SideInput(project_id="P1", broken_from="v-P1"),
                                              SideInput(project_id="P2"), "1.21.1", None)
    assert (summary, notes, key) == ("pantalla negra", ["n1", "n2"], "k1")


def test_marking_an_existing_pair_offers_to_join() -> None:
    client, knowledge, _ = knowledge_setup()
    knowledge.exists = "i7"
    response = client.post(f"/modpacks/{BUNDLE_ID}/conocimiento", headers=HTMX, data={
        "de": "1", "a": "2", "mod": "P1", "b": "P2", "summary": "negro", "notes": ["n1"], "key": "k1"})
    assert response.status_code == 200 and "Ya hay una entrada para estos mods" in response.text
    assert 'hx-post="/conocimiento/i7/sumarse"' in response.text and "Sumarse a la existente" in response.text
    joined = client.post("/conocimiento/i7/sumarse", headers=HTMX, data={"bundle": str(BUNDLE_ID), "notes": ["n1"]})
    assert joined.status_code == 200 and knowledge.joined == [("i7", str(BUNDLE_ID), ["n1"])]


def test_issue_exists_is_a_conflict() -> None:
    assert isinstance(IssueExists("i7"), HTTP_409_CONFLICT) and IssueExists("i7").issue_id == "i7"


# Where the base warns (Task 6: K7, K24, K-Q1, K-Q4).


def test_a_card_warns_of_a_confirmed_pair() -> None:
    client, _, modpack = knowledge_setup()
    modpack.known = {"P1": [warning().model_copy(update={"where": ["build 2", "Survival"]})]}
    text = client.get(f"/modpacks/{BUNDLE_ID}", params={"vista": "todos"}).text
    card = text.split('data-project="P1"')[1].split("</li>")[0]
    assert "⚠ Conocida" in card and "Iris 1.7+ ✕ Sodium" in card and "build 2, Survival" in card


def test_a_broken_version_links_the_suggestion() -> None:
    client, _, modpack = knowledge_setup()
    modpack.known = {"P1": [warning(broken=True)]}
    text = client.get(f"/modpacks/{BUNDLE_ID}", params={"vista": "todos"}).text
    assert f'hx-get="/modpacks/{BUNDLE_ID}/projects/P1/sugerencia"' in text and "Fijar la anterior que funcionó" in text


def test_an_old_build_page_keeps_its_warning() -> None:
    client, _, modpack = knowledge_setup()
    modpack.build_known = {"P1": [warning()]}
    text = client.get(f"/modpacks/{BUNDLE_ID}/builds/2").text
    assert "⚠ Conocida" in text and "Iris 1.7+ ✕ Sodium" in text


# The base's pages (Task 8: K20, K21, K26, K27, K-Q5).

from datetime import datetime, timezone

from src.model.modpack.known_issue import IssueSide, IssueVersion, KnownIssue
from src.model.modpack.note import VersionNote
from tests._actors import EDITOR, OPERATOR

AT = datetime(2026, 10, 27, tzinfo=timezone.utc)


def issue(status: str = "suspected", author: Actor = EDITOR, note_ids: Optional[list[str]] = None,
          seen_in: Optional[list[str]] = None, n: int = 1) -> KnownIssue:
    start = IssueVersion(version_id="v170", version_number="1.7.0", published=AT, game_versions=["1.21.1"], loaders=["fabric"])
    return KnownIssue.model_construct(
        id=f"6ae40b7c8901bede28d466{n:02d}", a=IssueSide(project_id="P1", title="Iris", broken_from=start),
        b=IssueSide(project_id="P2", title="Sodium"), game_version="1.21.1", loader=None, status=status,
        summary="pantalla negra", note_ids=note_ids or [], seen_in=seen_in or [str(BUNDLE_ID)], author=author.ref(),
        at=AT, updated_at=AT, rev=3, key="k")


class Base(Knowledge):
    """KnowledgeService for the base's pages."""

    def __init__(self, entries: list[KnownIssue]) -> None:
        super().__init__()
        self.entries = entries
        self.asked: list[tuple[Any, ...]] = []
        self.statuses: list[tuple[Any, ...]] = []
        self.withdrawn: list[str] = []

    async def find(self, project: Optional[str] = None, status: Optional[str] = None, game_version: Optional[str] = None,
                   before: Optional[datetime] = None, limit: int = 20) -> list[KnownIssue]:
        self.asked.append((project, status, game_version, before))
        return self.entries[:limit]

    async def get(self, issue_id: str) -> Any:
        return self.entries[0]

    async def issue_status(self, issue_id: str, rev: int, status: str, actor: Actor) -> Any:
        self.statuses.append((issue_id, rev, status))
        return self.entries[0]

    async def issue_withdraw(self, issue_id: str, actor: Actor) -> Any:
        self.withdrawn.append(issue_id)
        return self.entries[0]

    async def range_choices(self, issue: KnownIssue) -> dict[str, list[IssueVersion]]:
        return {"P1": [IssueVersion(version_id="v173", version_number="1.7.3", published=AT, game_versions=["1.21.1"],
                                    loaders=["fabric"])], "P2": []}


class BackingNotes:
    def __init__(self, notes: list[Any]) -> None:
        self.notes = notes

    async def find_by_ids(self, note_ids: list[str]) -> list[Any]:
        return [n for n in self.notes if str(n.id) in note_ids and not n.archived]


def base_setup(entries: list[KnownIssue], notes: Optional[list[Any]] = None, actor: Actor = EDITOR) -> tuple[Any, Base]:
    import main
    from dependency_injector import providers
    client, modpack, _, _, _ = setup_all(actor)
    base = Base(entries)
    main.container.knowledgeService.override(providers.Object(base))
    main.container.noteService.override(providers.Object(BackingNotes(notes or [])))
    return client, base


def backing(error: str = "SECRET-TRACE at sodium", archived: bool = False) -> Any:
    from beanie import PydanticObjectId
    return VersionNote.model_construct(id=PydanticObjectId("6ae40b7c8901bede28d46699"), bundle_id=str(BUNDLE_ID),
                                       build_number=2, verdict="fails", text="se cae al entrar", error=error,
                                       author=EDITOR.ref(), at=AT, archived=archived, rev=0, key="n")


def test_knowledge_lists_filters_and_pages() -> None:
    client, base = base_setup([issue(n=i) for i in range(20)])
    text = client.get("/conocimiento", params={"mod": "P1", "estado": "suspected", "mc": "1.21.1"}).text
    assert base.asked == [("P1", "suspected", "1.21.1", None)]
    assert "Iris 1.7.0+ ✕ Sodium" in text and "pantalla negra" in text and "Ver más" in text
    assert 'href="/conocimiento/6ae40b7c8901bede28d46601"' in text


def test_an_entry_never_shows_a_notes_error() -> None:
    client, _ = base_setup([issue(note_ids=["6ae40b7c8901bede28d46699"])], [backing()])
    text = client.get("/conocimiento/6ae40b7c8901bede28d46601").text
    assert "se cae al entrar" in text and "SECRET-TRACE" not in text


def test_an_entry_says_when_no_note_backs_it() -> None:
    client, _ = base_setup([issue(note_ids=["6ae40b7c8901bede28d46699"])], [backing(archived=True)])
    assert "Sin notas vigentes" in client.get("/conocimiento/6ae40b7c8901bede28d46601").text


def test_a_deleted_pack_shows_without_a_link() -> None:
    client, _ = base_setup([issue(seen_in=[str(BUNDLE_ID), "6ae40b7c8901bede28d46600"])])
    text = client.get("/conocimiento/6ae40b7c8901bede28d46601").text
    assert f'href="/modpacks/{BUNDLE_ID}"' in text and "un pack borrado" in text


def test_actions_follow_the_role() -> None:
    from dataclasses import replace
    other = replace(EDITOR, sub="ed-2")
    as_other = base_setup([issue(author=other)])[0].get("/conocimiento/6ae40b7c8901bede28d46601").text
    assert "Confirmar" not in as_other and "Descartar" not in as_other and "Retirar mi sospecha" not in as_other
    as_author = base_setup([issue()])[0].get("/conocimiento/6ae40b7c8901bede28d46601").text
    assert "Retirar mi sospecha" in as_author and "Editar" in as_author and "Confirmar" not in as_author
    client, base = base_setup([issue()], actor=OPERATOR)
    as_operator = client.get("/conocimiento/6ae40b7c8901bede28d46601").text
    assert "Confirmar" in as_operator and "Descartar" in as_operator
    done = client.post("/conocimiento/6ae40b7c8901bede28d46601/estado", headers=HTMX, data={"status": "confirmed", "rev": "3"})
    assert done.status_code == 200 and base.statuses == [("6ae40b7c8901bede28d46601", 3, "confirmed")]


def test_the_edit_dialog_offers_fixed_in_from_the_catalog() -> None:
    client, _ = base_setup([issue()])
    text = client.get("/conocimiento/6ae40b7c8901bede28d46601/editar", headers=HTMX).text
    assert "Arreglado en" in text and '<option value="v173">1.7.3</option>' in text and 'name="rev" value="3"' in text


def test_the_menu_has_knowledge_after_servers() -> None:
    client, _ = base_setup([])
    nav = client.get("/conocimiento").text.split('aria-label="Secciones"')[1].split("</nav>")[0]
    assert nav.index("Servidores") < nav.index("Conocimiento") < nav.index("Trabajos")


def test_the_edit_dialog_keeps_a_fix_the_catalog_does_not_offer() -> None:
    """Review 2: a «Arreglado en» missing from the choices (set through /v1, or older than the ones offered) stays
    chosen, so saving the summary never clears it."""
    fixed = IssueVersion(version_id="v180", version_number="1.8.0", published=AT, game_versions=["1.21.1"], loaders=["fabric"])
    entry = issue()
    entry.a = entry.a.model_copy(update={"fixed_in": fixed})
    client, _ = base_setup([entry])
    text = client.get("/conocimiento/6ae40b7c8901bede28d46601/editar", headers=HTMX).text
    assert '<option value="v180" selected>1.8.0</option>' in text
