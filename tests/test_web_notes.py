"""A note is written in a dialog (O8 tanda 2, P1 of d-7985ee-a68b5c; user-walk N7, N8, N14).

The dialog opens in #note-dialog, outside the page's lists, so no redraw of #mods takes a half-written
note. Its form carries a draft key (app.js keeps the draft in localStorage) and an idempotency key.
Saving answers the note into its list, out of band, and closes the dialog.
"""
from typing import Any, Optional

from src.model.modpack.note import NoteTarget
from src.model.users import Actor
from tests._actors import EDITOR
from src.model.pterodactyl import PterodactylServer
from tests.test_web_modpacks import BUNDLE_ID, HTMX, build, note, setup_all


class Notes:
    """NoteService for the panel: what it was asked to write, and the note it answers."""

    def __init__(self) -> None:
        self.notes: list[Any] = []
        self.asked: list[dict[str, Any]] = []
        self.created: list[tuple[Any, ...]] = []
        self.edited: list[tuple[Any, ...]] = []
        self.archived: list[tuple[Any, ...]] = []

    async def find(self, bundle_id: str, **filters: Any) -> list[Any]:
        self.asked.append(filters)
        return self.notes

    async def create(self, bundle_id: str, target: NoteTarget, verdict: str, text: str, error: Optional[str],
                     server_id: Optional[str], key: str, actor: Actor) -> Any:
        self.created.append((bundle_id, target, verdict, text, error, server_id, key, actor))
        return note(verdict, text, build_number=target.build_number, project_id=target.project_id,
                    version_id=target.version_id, version_number=target.version_number)

    async def get(self, note_id: str) -> Any:
        return note("fails", "antes", build_number=15)

    async def edit(self, note_id: str, verdict: str, text: str, error: Optional[str], rev: int, actor: Actor) -> Any:
        self.edited.append((note_id, verdict, text, rev, actor))
        return note(verdict, text, build_number=15)

    async def archive(self, note_id: str, actor: Actor, archived: bool = True) -> Any:
        self.archived.append((note_id, archived, actor))
        return note(build_number=15, archived=archived)


def notes_setup() -> tuple[Any, Notes, Any, Any]:
    import main
    from dependency_injector import providers
    client, modpack, _, _, ptero = setup_all()
    notes = Notes()
    main.container.noteService.override(providers.Object(notes))
    return client, notes, modpack, ptero


def dialog_tag(text: str) -> str:
    return text.split("<dialog")[1].split(">")[0]


def test_add_note_opens_the_dialog_outside_mods() -> None:
    client, _, _, ptero = notes_setup()
    ptero.servers = [PterodactylServer.model_construct(id="srv1", name="Survival", bundle_id=str(BUNDLE_ID),
                                                       is_suspended=False, status=None)]
    response = client.get(f"/modpacks/{BUNDLE_ID}/notas/nueva", params={"build": 15}, headers=HTMX)
    text = response.text
    assert response.status_code == 200 and "data-autoopen" in dialog_tag(text)
    assert "Nota sobre el build 15" in text and 'name="verdict"' in text and "Survival" in text
    page = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert page.index('id="note-dialog"') > page.index("</main>")


def test_the_dialog_warns_about_secrets_and_size() -> None:
    client, _, _, _ = notes_setup()
    text = client.get(f"/modpacks/{BUNDLE_ID}/notas/nueva", params={"mod": "P1", "version": "v-P1", "numero": "1.0"},
                      headers=HTMX).text
    assert "Nota sobre Sodium 1.0" in text
    assert "No pegues contraseñas; los tokens y las IPs se ocultan." in text
    assert "Si pasa de 64 KiB se guardan el principio y el final." in text


def test_the_form_carries_a_draft_key_and_an_idempotency_key() -> None:
    client, _, _, _ = notes_setup()
    first = client.get(f"/modpacks/{BUNDLE_ID}/notas/nueva", params={"build": 15}, headers=HTMX).text
    second = client.get(f"/modpacks/{BUNDLE_ID}/notas/nueva", params={"build": 15}, headers=HTMX).text
    assert f'data-draft="{BUNDLE_ID}:build-15"' in first
    key = first.split('name="key" value="')[1].split('"')[0]
    assert len(key) >= 16 and key not in second


def test_saving_answers_the_note_into_its_list_and_closes() -> None:
    client, notes, _, _ = notes_setup()
    response = client.post(f"/modpacks/{BUNDLE_ID}/notas", headers=HTMX, data={
        "build": "15", "verdict": "fails", "text": "pantalla negra", "error": "", "server": "", "key": "k-123"})
    text = response.text
    assert response.status_code == 200
    assert f'id="notes-build-15" hx-swap-oob="afterbegin"' in text and "pantalla negra" in text
    assert '<div id="note-dialog" hx-swap-oob="innerHTML"></div>' in text
    (bundle_id, target, verdict, said, error, server_id, key, actor) = notes.created[0]
    assert (bundle_id, target.build_number, verdict, said, error, server_id, key, actor) == (
        str(BUNDLE_ID), 15, "fails", "pantalla negra", None, None, "k-123", EDITOR)


def test_editing_and_archiving_answer_the_note_in_place() -> None:
    client, notes, _, _ = notes_setup()
    dialog = client.get("/notas/6adf6b88ebaffc9461992693/editar", headers=HTMX).text
    assert 'name="rev" value="0"' in dialog and "antes" in dialog
    edited = client.post("/notas/6adf6b88ebaffc9461992693", headers=HTMX,
                         data={"verdict": "works", "text": "ya anda", "error": "", "rev": "0"}).text
    assert "ya anda" in edited and 'hx-swap-oob="outerHTML"' in edited and notes.edited[0][1:4] == ("works", "ya anda", 0)
    archived = client.post("/notas/6adf6b88ebaffc9461992693/archivar", headers=HTMX).text
    assert "Nota archivada." in archived and "Deshacer" in archived and notes.archived[0][1] is True


def test_the_build_page_offers_add_note() -> None:
    client, _, modpack, _ = notes_setup()
    modpack.builds = [build(15)]
    text = client.get(f"/modpacks/{BUNDLE_ID}/builds/15").text
    assert f'hx-get="/modpacks/{BUNDLE_ID}/notas/nueva?build=15"' in text and 'id="notes-build-15"' in text
