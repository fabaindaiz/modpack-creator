"""The builds' list (O8 tanda 1, d-7985ee-a99db7 B2, B3; user-walk W15, W16, W20; H3 of d-7985ee-3bb53b)."""
from datetime import datetime, timezone
from typing import Any

from beanie import PydanticObjectId

from src.library.builds import BUILD_NAME_MAX, build_name, builds_view
from src.model.modpack import ModpackBuild


def builds(*numbers: int, **marks: Any) -> list[ModpackBuild]:
    """Builds of one pack, newest first as `build_find` gives them; `tests` and `starred` are sets of numbers."""
    tests, starred = marks.get("tests", set()), marks.get("starred", set())
    return [ModpackBuild.model_construct(bundle_id=PydanticObjectId(), number=n, created_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
                                         test=n in tests, starred=n in starred, name=None)
            for n in sorted(numbers, reverse=True)]


def numbers(listed: list[ModpackBuild]) -> list[int]:
    return [b.number for b in listed]


def test_a_name_is_trimmed_capped_and_empty_is_none() -> None:
    assert BUILD_NAME_MAX == 60
    assert build_name("  con Iris 1.7  ") == "con Iris 1.7"
    assert build_name("x" * 80) == "x" * 60
    assert build_name("   ") is None and build_name("") is None and build_name(None) is None


def test_starred_come_first_and_old_unstarred_fold() -> None:
    view = builds_view(builds(*range(1, 11), starred={2}), recommended=None, latest=10, installed=set(), starred_only=False)
    assert numbers(view.shown) == [2, 10, 9, 8] and numbers(view.folded) == [7, 6, 5, 4, 3, 1]


def test_what_matters_never_folds() -> None:
    """User-walk W16: the recommended, the latest, one installed on a server."""
    view = builds_view(builds(*range(1, 11)), recommended=3, latest=7, installed={4}, starred_only=False)
    assert numbers(view.shown) == [10, 9, 8, 7, 4, 3]


def test_a_failed_test_folds_once_newer_builds_exist() -> None:
    """H3 (d-7985ee-3bb53b): a test is never deleted; the newest build, test or not, never folds."""
    older = builds_view(builds(*range(1, 11), tests={5}), recommended=None, latest=10, installed=set(), starred_only=False)
    newest = builds_view(builds(*range(1, 11), tests={10}), recommended=None, latest=9, installed=set(), starred_only=False)
    assert 5 in numbers(older.folded) and 10 in numbers(newest.shown)


def test_previous_skips_gaps() -> None:
    """User-walk W20: «el anterior» is the build before that exists; the first has none."""
    assert builds_view(builds(1, 2, 4), recommended=None, latest=4, installed=set(), starred_only=False).previous == {2: 1, 4: 2}


def test_only_starred_shows_the_starred_and_folds_the_rest() -> None:
    view = builds_view(builds(*range(1, 6), starred={1, 3}), recommended=5, latest=5, installed=set(), starred_only=True)
    assert numbers(view.shown) == [3, 1] and numbers(view.folded) == [5, 4, 2]


def test_two_hundred_builds_fold_all_but_the_shown() -> None:
    view = builds_view(builds(*range(1, 201)), recommended=None, latest=200, installed=set(), starred_only=False)
    assert numbers(view.shown) == [200, 199, 198] and len(view.folded) == 197
