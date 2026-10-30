"""Notes on a pack's builds and mods' versions (O8 tanda 2, d-7985ee-a99db7 D3–D5, D10, D11; P2, P3 of
d-7985ee-a68b5c)."""
from datetime import datetime, timezone
from typing import Callable, Optional

from src.database.modpack import ModpackDatabase
from src.database.note import NoteDatabase
from src.database.pterodactyl import PterodactylDatabase
from src.exceptions import *
from src.library.notes import cut_error, mask_secrets
from src.model.audit import AuditTarget
from src.model.modpack.note import NoteTarget, NoteVersion, Verdict, VersionNote
from src.model.users import Actor
from src.services.audit import AuditSink

# A comment's length; the error has its own cut (`cut_error`).
TEXT_MAX = 4000
NOTE_BUSY = "La nota cambió mientras tanto; vuelve a abrirla"
# A key already used by another person, or in another pack (review of tanda 2): never their note.
KEY_TAKEN = "Esa clave ya es de otra nota: vuelve a abrir el formulario"


def _bundle(bundle_id: str) -> AuditTarget:
    return AuditTarget(kind="bundle", id=bundle_id)


def _error(error: Optional[str]) -> Optional[str]:
    """A pasted error as stored: masked, then cut (P3, P2); empty is none."""
    if not error or not error.strip():
        return None
    return cut_error(mask_secrets(error))[0]


class NoteService:
    def __init__(self, noteDB: NoteDatabase, audit: AuditSink, pterodactylDB: Optional[PterodactylDatabase] = None,
                 modpackDB: Optional[ModpackDatabase] = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.noteDB = noteDB
        self.audit = audit
        self.pterodactylDB = pterodactylDB
        self.modpackDB = modpackDB
        self.clock = clock

    async def create(self, bundle_id: str, target: NoteTarget, verdict: Verdict, text: str, error: Optional[str],
                     server_id: Optional[str], key: str, actor: Actor) -> VersionNote:
        """Write a note on a build or a mod's version (editor). The same `key` is the same note: a form
        sent twice writes one (user-walk N14), but only for its author in its pack; anyone else is a 409
        (review of tanda 2). The build and the server are the pack's (422); the server's name is kept as
        it is now (N22)."""
        if target.build_number is None and not (target.project_id and target.version_id):
            raise HTTP_422_UNPROCESSABLE_ENTITY("Una nota es de un build o de la versión de un mod")
        if target.build_number is not None and self.modpackDB is not None:
            try:
                await self.modpackDB.build_get(bundle_id, target.build_number)
            except HTTPException as missing:
                if missing.status_code != 404:
                    raise
                raise HTTP_422_UNPROCESSABLE_ENTITY(f"El build {target.build_number} no es de este pack") from None
        server_name = None
        if server_id and self.pterodactylDB is not None:
            server = await self.pterodactylDB.server_get(server_id)
            if server.bundle_id != bundle_id:
                raise HTTP_422_UNPROCESSABLE_ENTITY("Ese servidor no está vinculado a este pack")
            server_name = server.name
        note = await self.noteDB.insert(VersionNote(
            bundle_id=bundle_id, **target.model_dump(), server_id=server_id or None, server_name=server_name,
            verdict=verdict, text=text.strip()[:TEXT_MAX], error=_error(error), author=actor.ref(), at=self.clock(),
            key=key))
        if (note.bundle_id, note.author.iss, note.author.sub) != (bundle_id, actor.iss, actor.sub):
            raise HTTP_409_CONFLICT(KEY_TAKEN)
        await self.audit.record(actor, "note.create", _bundle(bundle_id), {"note_id": str(note.id), "verdict": verdict})
        return note

    async def get(self, note_id: str) -> VersionNote:
        return await self.noteDB.get(note_id)

    async def edit(self, note_id: str, verdict: Verdict, text: str, error: Optional[str], rev: int,
                   actor: Actor) -> VersionNote:
        """Edit a note: only its author (403), and only from the revision read (409, user-walk N15). What
        it said before is kept (D11)."""
        note = await self.noteDB.get(note_id)
        if (note.author.iss, note.author.sub) != (actor.iss, actor.sub):
            raise HTTP_403_FORBIDDEN("Solo quien escribió la nota la edita")
        before = NoteVersion(verdict=note.verdict, text=note.text, error=note.error, at=note.edited_at or note.at)
        kept = note.error if (error or None) == note.error else _error(error)
        if not await self.noteDB.update(note_id, rev, {"verdict": verdict, "text": text.strip()[:TEXT_MAX],
                                                       "error": kept, "edited_at": self.clock()}, before):
            raise HTTP_409_CONFLICT(NOTE_BUSY)
        await self.audit.record(actor, "note.edit", _bundle(note.bundle_id), {"note_id": note_id})
        return await self.noteDB.get(note_id)

    async def archive(self, note_id: str, actor: Actor, archived: bool = True) -> VersionNote:
        """Archive a note, or bring it back: its author does, and an operator does anyone's (D10)."""
        note = await self.noteDB.get(note_id)
        # Not `operator_only`: an editor archives their own note; only another's is an operator's.
        if (note.author.iss, note.author.sub) != (actor.iss, actor.sub) and not actor.is_operator:
            raise HTTP_403_FORBIDDEN("Solo un operador archiva la nota de otra persona")
        if not await self.noteDB.update(str(note.id), note.rev, {"archived": archived}):
            raise HTTP_409_CONFLICT(NOTE_BUSY)
        await self.audit.record(actor, "note.archive" if archived else "note.unarchive", _bundle(note.bundle_id),
                                {"note_id": str(note.id)})
        return await self.noteDB.get(str(note.id))

    async def find_by_ids(self, note_ids: list[str]) -> list[VersionNote]:
        """The notes behind an entry of the knowledge base, never an archived one (O8 tanda 3, a read)."""
        return await self.noteDB.find_by_ids(note_ids)

    async def find(self, bundle_id: str, *, build_number: Optional[int] = None, project_id: Optional[str] = None,
                   version_ids: Optional[list[str]] = None, archived: bool = False, limit: int = 200) -> list[VersionNote]:
        """A pack's notes, newest first (a read)."""
        return await self.noteDB.find(bundle_id, build_number=build_number, project_id=project_id,
                                      version_ids=version_ids, archived=archived, limit=limit)
