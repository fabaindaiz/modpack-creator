"""When a known issue applies to a set of versions (O8 tanda 3, d-7985ee-57d185; user-walk K3, K4, K14, K18):
by publication date, as the floors and `direction` (d-7985ee-6dc34c), and only on the branch of the range's
ends: a backport for another Minecraft version is never inside a range, nor outside it."""
import re
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
    «Rota desde» («in» when the only end is «Arreglado en»); "out" before `broken_from` or from `fixed_in` on; "in" otherwise, and with no ends."""
    end = side.broken_from or side.fixed_in
    if end is None:
        return "in"
    if not _same_branch(end, version):
        # With only «Arreglado en», a version of another branch has no fix known: it is still broken (review 1).
        return "in" if side.broken_from is None else "branch"
    published = as_utc(version.published)
    if side.broken_from is not None and published < as_utc(side.broken_from.published):
        return "out"
    if side.fixed_in is not None and published >= as_utc(side.fixed_in.published):
        return "out"
    return "in"


def issue_applies(issue: KnownIssue, versions: Mapping[str, VersionFacts], game_version: str, loader: str) -> bool:
    """Whether the issue is in `versions` (project id -> the version's facts): each side present and in its
    range, on the issue's Minecraft versions if it has them (widened ones too, K10), and on its loader, either version's own (a Fabric mod
    runs on NeoForge through a bridge) or else the pack's."""
    if issue.game_version is not None and game_version not in [issue.game_version, *issue.also_game_versions]:
        return False
    sides = [issue.a] + ([issue.b] if issue.b is not None else [])
    if any(side.project_id not in versions for side in sides):
        return False
    if issue.loader is not None:
        # Either mod's own loaders (one may run through a bridge), or the pack's (owner, 2026-10-30, K18).
        own = {name for side in sides for name in versions[side.project_id].loaders}
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


# Minecraft versions whose mods usually keep working across each other, from Fabric's announcements (one announcement
# per group): `docs/research/2026-10-30-compatibilidad-versiones-minecraft.md`, «La regla que aplica el panel». A
# version not listed is its own group; from 26.1 on, year.drop is the group (26.1, 26.1.1, 26.1.2). No loader
# promises it: the panel only says «probablemente» (K10 of d-7985ee-c0cbfb).
HOTFIX_GROUPS: tuple[frozenset[str], ...] = tuple(frozenset(group) for group in (
    ("1.16.2", "1.16.3", "1.16.4", "1.16.5"),
    ("1.18", "1.18.1", "1.18.2"),  # an assumption of the research
    ("1.19", "1.19.1", "1.19.2"),
    ("1.20", "1.20.1"),
    ("1.20.3", "1.20.4"),  # an assumption of the research; 1.20.2 → 1.20.3 breaks
    ("1.20.5", "1.20.6"),
    ("1.21", "1.21.1"),
    ("1.21.2", "1.21.3"),
    ("1.21.6", "1.21.7", "1.21.8"),
    ("1.21.9", "1.21.10"),
))
_YEARLY = re.compile(r"(\d+)\.(\d+)(?:\.\d+)?")


def hotfix_group(game_version: str) -> str:
    """The group of a Minecraft version, as a key: its table group, its year.drop from 26.1 on, or itself."""
    for group in HOTFIX_GROUPS:
        if game_version in group:
            return min(group)
    yearly = _YEARLY.fullmatch(game_version)
    if yearly is not None and int(yearly.group(1)) >= 26:
        return f"{yearly.group(1)}.{yearly.group(2)}"
    return game_version


def same_hotfix_group(first: str, second: str) -> bool:
    """Whether two Minecraft versions are in one hotfix group: mods probably work on both (rule 1 of the research)."""
    return hotfix_group(first) == hotfix_group(second)
