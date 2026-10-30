"""The knowledge base (O8 tanda 3, d-7985ee-a99db7 D6–D10, D13; d-7985ee-57d185): marking a pair of mods, or one
version alone, as a suspicion; an operator confirms or rejects it. It only warns (D8)."""
import asyncio
from datetime import datetime, timezone
from typing import Callable, Literal, Mapping, Optional, Sequence

from pydantic import BaseModel

from src.database.knowledge import KnowledgeDatabase
from src.database.modpack import ModpackDatabase
from src.database.modrinth import ModrinthDatabase
from src.database.note import NoteDatabase
from src.exceptions import *
from src.library.api.client.modrinth import ModrinthAPI
from src.library.auth.roles import operator_only
from src.library.changes import VersionFacts
from src.library.knowledge import KnownWarning, issue_applies, side_state, warning_of
from src.library.utils import as_utc
from src.model.audit import AuditTarget
from src.model.modpack.known_issue import IssueSide, IssueVersion, KnownIssue
from src.model.modrinth import ModrinthVersion
from src.model.users import Actor
from src.services.audit import AuditSink

SUMMARY_MAX = 200
# The entries read for one set of versions: far more than a pack meets.
WARNINGS_READ = 500
# How many of a mod's catalog versions «Arreglado en» offers.
RANGE_CHOICES = 200
ISSUE_BUSY = "La entrada cambió mientras tanto; vuelve a abrirla"
ISSUE_EXISTS = "Ya hay una entrada para estos mods"


async def warnings_for(knowledgeDB: KnowledgeDatabase, modrinthDB: ModrinthDatabase, versions: Mapping[str, str],
                       game_version: str, loader: str, *, suspected: bool = False) -> dict[str, list[KnownWarning]]:
    """The warnings of these versions (project id -> version id) on this Minecraft version and loader, by each
    project they name: one read of the base for these mods, then the catalog's dates of only the versions of the
    mods that match; never Modrinth (user-walk K5, K6). Confirmed entries only, and suspicions if asked (the
    comparison, K-Q1); a version the catalog lacks warns of nothing. It only warns (D8)."""
    return (await warnings_many(knowledgeDB, modrinthDB, [(versions, game_version, loader)], suspected=suspected))[0]


def _projects(issue: KnownIssue) -> list[str]:
    return [issue.a.project_id] + ([issue.b.project_id] if issue.b else [])


async def warnings_many(knowledgeDB: KnowledgeDatabase, modrinthDB: ModrinthDatabase,
                        sources: Sequence[tuple[Mapping[str, str], str, str]], *,
                        suspected: bool = False) -> list[dict[str, list[KnownWarning]]]:
    """`warnings_for` of several sets of versions (versions, Minecraft version, loader), in their order: one read of
    the base for all their mods and one of the catalog for all the versions that match, whatever the number of sets
    (the dashboard, review B8). The read's cap grows with the sets: an entry whose mods are in different sets is read
    and warns in none."""
    found: list[dict[str, list[KnownWarning]]] = [{} for _ in sources]
    every = sorted({p for versions, _, _ in sources for p in versions})
    if not every:
        return found
    statuses = ["confirmed", "suspected"] if suspected else ["confirmed"]
    read = await knowledgeDB.find(within=every, status=statuses, limit=WARNINGS_READ * len(sources))
    issues = [[i for i in read if all(p in versions for p in _projects(i))] for versions, _, _ in sources]
    wanted = sorted({versions[p] for (versions, _, _), mine in zip(sources, issues) for i in mine for p in _projects(i)})
    if not wanted:
        return found
    catalog = {v.version_id: v for v in await modrinthDB.version_find_by_version_ids(wanted)}
    for n, ((versions, game_version, loader), mine) in enumerate(zip(sources, issues)):
        facts = {p: VersionFacts(published=v.date_published, game_versions=tuple(v.game_versions),
                                 loaders=tuple(v.loaders))
                 for p in {p for i in mine for p in _projects(i)}
                 if (v := catalog.get(versions[p])) is not None and v.project_id == p}
        for issue in mine:
            if issue_applies(issue, facts, game_version, loader):
                warning = warning_of(issue)
                for project_id in warning.project_ids:
                    found[n].setdefault(project_id, []).append(warning)
    return found


def merge_warnings(sources: list[tuple[str, dict[str, list[KnownWarning]]]]) -> dict[str, list[KnownWarning]]:
    """The warnings of several sources of a pack (its build in force, each linked server's scan, user-walk K7), one
    per entry and project, each with where it applies, in the sources' order."""
    found: dict[str, dict[str, KnownWarning]] = {}
    for where, warnings in sources:
        for project_id, listed in warnings.items():
            for warning in listed:
                kept = found.setdefault(project_id, {}).setdefault(warning.issue_id, warning.model_copy(update={"where": []}))
                if where not in kept.where:
                    kept.where.append(where)
    return {project_id: list(by_issue.values()) for project_id, by_issue in found.items()}


class IssueExists(HTTP_409_CONFLICT):
    """A pair already in the base, not rejected: its id, so the editor joins it (user-walk K10)."""

    def __init__(self, issue_id: str) -> None:
        super().__init__(f"{ISSUE_EXISTS}: {issue_id}")
        self.issue_id = issue_id


class SideInput(BaseModel):
    """A side as it is asked for: the mod and the version ids of its range, if any."""
    project_id: str
    broken_from: Optional[str] = None
    fixed_in: Optional[str] = None


def _target(issue_id: str) -> AuditTarget:
    return AuditTarget(kind="knowledge", id=issue_id)


def _mine(issue: KnownIssue, actor: Actor) -> bool:
    return (issue.author.iss, issue.author.sub) == (actor.iss, actor.sub)


class KnowledgeService:
    def __init__(self, knowledgeDB: KnowledgeDatabase, modrinthDB: ModrinthDatabase, modrinthAPI: ModrinthAPI,
                 audit: AuditSink, modpackDB: ModpackDatabase, noteDB: NoteDatabase,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.knowledgeDB = knowledgeDB
        self.modrinthDB = modrinthDB
        self.modrinthAPI = modrinthAPI
        self.audit = audit
        self.modpackDB = modpackDB
        self.noteDB = noteDB
        self.clock = clock

    async def _of_pack(self, bundle_id: str, note_ids: list[str]) -> list[str]:
        """These notes, once each, after checking the pack exists (404) and each note is one of its own, not
        archived (422, review B7): an entry backed by another pack's note would show it under the wrong pack."""
        await self.modpackDB.bundle_get(bundle_id)
        wanted = list(dict.fromkeys(note_ids))
        if not wanted:
            return []
        own = {str(n.id) for n in await self.noteDB.find_by_ids(wanted) if n.bundle_id == bundle_id}
        if any(n not in own for n in wanted):
            raise HTTP_422_UNPROCESSABLE_ENTITY("Una de las notas no es de este pack")
        return wanted

    async def _versions(self, ids: list[str]) -> dict[str, ModrinthVersion]:
        """These versions from the catalog, and the ones it lacks from Modrinth (a read, never a write)."""
        found = {v.version_id: v for v in await self.modrinthDB.version_find_by_version_ids(ids)}
        missing = [i for i in ids if i not in found]
        if missing:
            found |= {v.version_id: v for v in await self.modrinthAPI.version_get_many(missing)}
        return found

    async def _titles(self, project_ids: list[str]) -> dict[str, str]:
        found = {p.project_id: p.title for p in await self.modrinthDB.project_find_by_modrinth_id(project_ids)}
        missing = [p for p in project_ids if p not in found]
        if missing:
            found |= {p.project_id: p.title for p in await self.modrinthAPI.project_get_many(missing)}
        return found

    async def _sides(self, a: SideInput, b: Optional[SideInput]) -> tuple[IssueSide, Optional[IssueSide]]:
        """The sides as stored: each version checked to be its mod's (422, user-walk K15), the range forward,
        the pair in a fixed order (K10), the ends kept whole (K5)."""
        given = [a] + ([b] if b is not None else [])
        if b is not None and a.project_id == b.project_id:
            raise HTTP_422_UNPROCESSABLE_ENTITY("Una incompatibilidad es entre dos mods distintos")
        ids = sorted({v for s in given for v in (s.broken_from, s.fixed_in) if v})
        versions, titles = await asyncio.gather(self._versions(ids), self._titles([s.project_id for s in given]))

        def end(side: SideInput, version_id: Optional[str]) -> Optional[IssueVersion]:
            if not version_id:
                return None
            found = versions.get(version_id)
            if found is None or found.project_id != side.project_id:
                raise HTTP_422_UNPROCESSABLE_ENTITY(f"La versión {version_id} no es de este mod")
            return IssueVersion(version_id=found.version_id, version_number=found.version_number,
                                published=found.date_published, game_versions=found.game_versions,
                                loaders=found.loaders)

        sides = []
        for side in given:
            start, stop = end(side, side.broken_from), end(side, side.fixed_in)
            if start is not None and stop is not None and as_utc(stop.published) <= as_utc(start.published):
                raise HTTP_422_UNPROCESSABLE_ENTITY("«Arreglado en» tiene que ser posterior a «Rota desde»")
            sides.append(IssueSide(project_id=side.project_id, title=titles.get(side.project_id, side.project_id),
                                   broken_from=start, fixed_in=stop))
        sides.sort(key=lambda s: s.project_id)
        return sides[0], sides[1] if len(sides) > 1 else None

    async def issue_mark(self, bundle_id: str, a: SideInput, b: Optional[SideInput], game_version: Optional[str],
                   loader: Optional[str], summary: str, note_ids: list[str], key: str, actor: Actor) -> KnownIssue:
        """Mark two mods, or one version alone (`b` None, K-Q4), as a suspicion seen in this pack (editor).
        The same `key` is the same entry; a pair already in the base, not rejected, is a 409 that names it."""
        if not summary.strip():
            raise HTTP_422_UNPROCESSABLE_ENTITY("Escribe en una línea qué pasa")
        note_ids = await self._of_pack(bundle_id, note_ids)
        first, second = await self._sides(a, b)
        existing = await self.knowledgeDB.find_pair(first.project_id, second.project_id if second else None)
        if existing is not None and existing.key != key:
            raise IssueExists(str(existing.id))
        now = self.clock()
        issue = await self.knowledgeDB.insert(KnownIssue(
            a=first, b=second, game_version=game_version or None, loader=loader or None, status="suspected",
            summary=summary.strip()[:SUMMARY_MAX], note_ids=note_ids, seen_in=[bundle_id],
            author=actor.ref(), at=now, updated_at=now, key=key))
        await self.audit.record(actor, "knowledge.mark", _target(str(issue.id)), {"bundle_id": bundle_id})
        return issue

    async def issue_join(self, issue_id: str, bundle_id: str, note_ids: list[str], actor: Actor) -> KnownIssue:
        """Add this pack and these notes to an entry, without repeating them (editor)."""
        note_ids = await self._of_pack(bundle_id, note_ids)
        issue = await self.knowledgeDB.get(issue_id)
        if not await self.knowledgeDB.update(issue_id, issue.rev, {"updated_at": self.clock()},
                                             add={"seen_in": [bundle_id], "note_ids": note_ids}):
            raise HTTP_409_CONFLICT(ISSUE_BUSY)
        await self.audit.record(actor, "knowledge.join", _target(issue_id), {"bundle_id": bundle_id})
        return await self.knowledgeDB.get(issue_id)

    async def issue_edit(self, issue_id: str, rev: int, a: SideInput, b: Optional[SideInput], game_version: Optional[str],
                   loader: Optional[str], summary: str, actor: Actor) -> KnownIssue:
        """Edit an entry's mods, ranges and where it was seen: a suspicion by its author or an operator, a
        confirmed or rejected entry by an operator (403, user-walk K22); from the revision read (409)."""
        issue = await self.knowledgeDB.get(issue_id)
        # Not `operator_only`: an editor edits their own suspicion.
        if not actor.is_operator and (issue.status != "suspected" or not _mine(issue, actor)):
            raise HTTP_403_FORBIDDEN("Solo un operador edita una entrada confirmada o ajena")
        first, second = await self._sides(a, b)
        fields = {"a": first.model_dump(), "b": second.model_dump() if second else None,
                  "game_version": game_version or None, "loader": loader or None,
                  "summary": (summary.strip() or issue.summary)[:SUMMARY_MAX], "updated_at": self.clock()}
        if not await self.knowledgeDB.update(issue_id, rev, fields):
            raise HTTP_409_CONFLICT(ISSUE_BUSY)
        await self.audit.record(actor, "knowledge.edit", _target(issue_id), {})
        return await self.knowledgeDB.get(issue_id)

    @operator_only
    async def issue_status(self, issue_id: str, rev: int, status: Literal["suspected", "confirmed", "rejected"],
                         actor: Actor) -> KnownIssue:
        """Confirm an entry, reject it (a false alarm) or send it back to suspicion (operator, D10)."""
        if not await self.knowledgeDB.update(issue_id, rev, {"status": status, "updated_at": self.clock()}):
            await self.knowledgeDB.get(issue_id)
            raise HTTP_409_CONFLICT(ISSUE_BUSY)
        await self.audit.record(actor, "knowledge.status", _target(issue_id), {"status": status})
        return await self.knowledgeDB.get(issue_id)

    async def issue_withdraw(self, issue_id: str, actor: Actor) -> KnownIssue:
        """Its author takes back their own suspicion (user-walk K23): it becomes rejected. Another person's is
        403; an entry no longer suspected is 409."""
        issue = await self.knowledgeDB.get(issue_id)
        if not _mine(issue, actor):
            raise HTTP_403_FORBIDDEN("Solo quien la marcó retira una sospecha")
        if issue.status != "suspected":
            raise HTTP_409_CONFLICT("Ya no es una sospecha: la decide un operador")
        if not await self.knowledgeDB.update(issue_id, issue.rev, {"status": "rejected", "updated_at": self.clock()}):
            raise HTTP_409_CONFLICT(ISSUE_BUSY)
        await self.audit.record(actor, "knowledge.withdraw", _target(issue_id), {})
        return await self.knowledgeDB.get(issue_id)

    async def get(self, issue_id: str) -> KnownIssue:
        return await self.knowledgeDB.get(issue_id)

    async def find(self, project: Optional[str] = None, status: Optional[str] = None,
                   game_version: Optional[str] = None, before: Optional[datetime] = None,
                   before_id: Optional[str] = None, limit: int = 20) -> list[KnownIssue]:
        """Entries newest first, of a mod, in a state, on a Minecraft version (a read); a page after (`before`,
        `before_id`)."""
        return await self.knowledgeDB.find(project=project, status=[status] if status else None,
                                           game_version=game_version, before=before, before_id=before_id, limit=limit)

    async def range_choices(self, issue: KnownIssue) -> dict[str, list[IssueVersion]]:
        """For «Arreglado en», by project: the catalog's versions of each side's mod, newest first, after its
        «Rota desde» and on its branch (user-walk K14), or on the entry's Minecraft version (a read, never Modrinth)."""
        choices: dict[str, list[IssueVersion]] = {}
        for side in [issue.a] + ([issue.b] if issue.b is not None else []):
            start = side.model_copy(update={"fixed_in": None})
            kept = []
            for v in await self.modrinthDB.version_find_by_project(side.project_id, RANGE_CHOICES):
                facts = VersionFacts(published=v.date_published, game_versions=tuple(v.game_versions),
                                     loaders=tuple(v.loaders))
                if side.broken_from is not None and v.version_id == side.broken_from.version_id:
                    continue
                if side_state(start, facts) != "in":
                    continue
                if side.broken_from is None and issue.game_version and issue.game_version not in v.game_versions:
                    continue
                kept.append(IssueVersion(version_id=v.version_id, version_number=v.version_number,
                                         published=v.date_published, game_versions=v.game_versions, loaders=v.loaders))
            choices[side.project_id] = kept
        return choices

    async def suspected_count(self) -> int:
        return await self.knowledgeDB.count("suspected")
