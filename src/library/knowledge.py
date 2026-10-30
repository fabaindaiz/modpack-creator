"""When a known issue applies to a set of versions (O8 tanda 3, d-7985ee-57d185; user-walk K3, K4, K14, K18):
by publication date, as the floors and `direction` (d-7985ee-6dc34c), and only on the branch of the range's
ends: a backport for another Minecraft version is never inside a range, nor outside it."""
from typing import Literal, Mapping, Optional

from pydantic import BaseModel

from src.library.changes import VersionFacts
from src.library.utils import as_utc
from src.model.modpack.known_issue import IssueSide, IssueVersion, KnownIssue

SideState = Literal["in", "out", "branch"]


def _same_branch(end: IssueVersion, version: VersionFacts) -> bool:
    return bool(set(end.game_versions) & set(version.game_versions)) and bool(set(end.loaders) & set(version.loaders))


def side_state(side: IssueSide, version: VersionFacts) -> SideState:
    """Whether `version` is in the side's range: "branch" when it shares no Minecraft version or loader with
    the range's end; "out" before `broken_from` or from `fixed_in` on; "in" otherwise, and with no ends."""
    end = side.broken_from or side.fixed_in
    if end is None:
        return "in"
    if not _same_branch(end, version):
        return "branch"
    published = as_utc(version.published)
    if side.broken_from is not None and published < as_utc(side.broken_from.published):
        return "out"
    if side.fixed_in is not None and published >= as_utc(side.fixed_in.published):
        return "out"
    return "in"


def issue_applies(issue: KnownIssue, versions: Mapping[str, VersionFacts], game_version: str, loader: str) -> bool:
    """Whether the issue is in `versions` (project id -> the version's facts): each side present and in its
    range, on the issue's Minecraft version if it has one, and on its loader, the version's own (a Fabric mod
    runs on NeoForge through a bridge) or else the pack's."""
    if issue.game_version is not None and issue.game_version != game_version:
        return False
    sides = [issue.a] + ([issue.b] if issue.b is not None else [])
    if any(side.project_id not in versions for side in sides):
        return False
    if issue.loader is not None:
        own = versions[issue.a.project_id].loaders
        if issue.loader not in own and issue.loader != loader:
            return False
    return all(side_state(side, versions[side.project_id]) == "in" for side in sides)


def _side_text(side: IssueSide) -> str:
    if side.broken_from is not None and side.fixed_in is not None:
        return f"{side.title} {side.broken_from.version_number} a {side.fixed_in.version_number} (excluida)"
    if side.broken_from is not None:
        return f"{side.title} {side.broken_from.version_number}+"
    if side.fixed_in is not None:
        return f"{side.title} antes de {side.fixed_in.version_number}"
    return side.title


class KnownWarning(BaseModel):
    """What a known issue says where it applies (D8: only a warning)."""
    issue_id: str
    status: str
    summary: str
    project_ids: list[str]
    text: str
    broken: bool
    # Where it applies, when a pack's page puts several sources together («build 14», «Survival»).
    where: list[str] = []


def warning_of(issue: KnownIssue, where: Optional[list[str]] = None) -> KnownWarning:
    broken = issue.b is None
    text = f"Versión rota: {_side_text(issue.a)}" if broken else f"{_side_text(issue.a)} ✕ {_side_text(issue.b)}"  # type: ignore[arg-type]
    return KnownWarning(issue_id=str(issue.id), status=issue.status, summary=issue.summary,
                        project_ids=[issue.a.project_id] + ([issue.b.project_id] if issue.b else []),
                        text=text, broken=broken, where=where or [])
