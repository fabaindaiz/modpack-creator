"""The builds of a pack as the panel lists them (O8 tanda 1, d-7985ee-a99db7): their names and, with
the list, what is shown and what folds. Pure."""
from dataclasses import dataclass, field
from typing import Optional, Sequence

from src.model.modpack import ModpackBuild

# A build's name (B2): short, one line in the list and in the server's selects.
BUILD_NAME_MAX = 60


def build_name(name: Optional[str]) -> Optional[str]:
    """A name as it is stored: trimmed and cut at BUILD_NAME_MAX; empty or only spaces is no name
    (user-walk W15)."""
    trimmed = (name or "").strip()[:BUILD_NAME_MAX].strip()
    return trimmed or None

# The newest builds always shown (O8 B3): the rest that matters is added, the others fold.
NEWEST_SHOWN = 3


@dataclass(frozen=True)
class BuildsView:
    """A pack's builds as the list shows them: `shown`, then `folded` under «N builds anteriores»;
    `previous` is the number of the build before each that exists (user-walk W20)."""
    shown: list[ModpackBuild]
    folded: list[ModpackBuild]
    previous: dict[int, int]
    # Each build's verdict and how many notes say it (O8 tanda 2, T5); a build without notes is absent.
    verdicts: dict[int, tuple[str, int]] = field(default_factory=dict)


def builds_view(builds: Sequence[ModpackBuild], recommended: Optional[int], latest: Optional[int],
                installed: set[int], starred_only: bool,
                verdicts: Optional[dict[int, tuple[str, int]]] = None) -> BuildsView:
    """The builds (newest first) as listed (O8 B3): the starred first, newest first, then the others.
    Never folded (user-walk W16): the recommended, the latest, one installed on a server and the
    newest builds, a test among them (H3: an older test folds like any other). With `starred_only`
    only the starred are shown."""
    ordered = sorted(builds, key=lambda b: b.number, reverse=True)
    newest = {b.number for b in ordered[:NEWEST_SHOWN]}
    # A stable build too (T3b): «Recomendar el último estable» reads it here.
    kept = {n for n in (recommended, latest) if n is not None} | installed | newest | {b.number for b in ordered if b.stable}

    def shown(build: ModpackBuild) -> bool:
        return build.starred if starred_only else build.starred or build.number in kept

    visible = [b for b in ordered if b.starred and shown(b)] + [b for b in ordered if not b.starred and shown(b)]
    numbers = [b.number for b in reversed(ordered)]
    return BuildsView(shown=visible, folded=[b for b in ordered if not shown(b)],
                      previous={after: before for before, after in zip(numbers, numbers[1:])}, verdicts=verdicts or {})

