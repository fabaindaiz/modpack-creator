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
