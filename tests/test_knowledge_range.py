"""When a version falls in a known issue's range (O8 tanda 3, d-7985ee-57d185 K-Q2, K-Q4; user-walk K3, K4,
K14, K18): by publication date, and only on the branch of the range's ends."""
from datetime import datetime, timezone

from src.library.changes import VersionFacts
from src.library.knowledge import issue_applies, side_state
from src.model.modpack.known_issue import IssueSide, IssueVersion, KnownIssue
from src.model.users.actor import ActorRef


def day(n: int) -> datetime:
    return datetime(2026, 10, n, tzinfo=timezone.utc)


def version(vid: str, n: int, mc: str = "1.21.1", loader: str = "fabric") -> IssueVersion:
    return IssueVersion(version_id=vid, version_number=vid, published=day(n), game_versions=[mc], loaders=[loader])


def facts(n: int, mc: str = "1.21.1", loader: str = "fabric") -> VersionFacts:
    return VersionFacts(published=day(n), game_versions=(mc,), loaders=(loader,))


AUTHOR = ActorRef(iss="https://id.example", sub="ana", name="Ana")


def issue(a: IssueSide, b: IssueSide | None = None, game_version: str | None = None,
          loader: str | None = None) -> KnownIssue:
    # Built without MongoDB, as the panel tests build their documents.
    return KnownIssue.model_construct(a=a, b=b, game_version=game_version, loader=loader, status="confirmed", summary="negro",
                      author=AUTHOR, at=day(1), updated_at=day(1), key="k")


def test_a_version_before_broken_from_is_out() -> None:
    side = IssueSide(project_id="iris", title="Iris", broken_from=version("1.7.0", 10))
    assert side_state(side, facts(9)) == "out"
    assert side_state(side, facts(10)) == "in"


def test_fixed_in_is_the_first_good_version() -> None:
    side = IssueSide(project_id="iris", title="Iris", broken_from=version("1.7.0", 10), fixed_in=version("1.7.3", 20))
    assert side_state(side, facts(19)) == "in"
    assert side_state(side, facts(20)) == "out"


def test_a_backport_on_another_branch_is_not_in_range() -> None:
    """Sodium 0.5.11 for 1.20.1 comes out after 0.6.0 for 1.21: it is another branch, never inside."""
    side = IssueSide(project_id="sodium", title="Sodium", broken_from=version("0.6.0", 10, mc="1.21.1"))
    assert side_state(side, facts(15, mc="1.20.1")) == "branch"


def test_no_range_is_any_version() -> None:
    assert side_state(IssueSide(project_id="iris", title="Iris"), facts(1, mc="1.16.5")) == "in"


def test_a_pair_applies_only_when_both_mods_are_in() -> None:
    found = issue(IssueSide(project_id="iris", title="Iris", broken_from=version("1.7.0", 10)),
                  IssueSide(project_id="sodium", title="Sodium"))
    assert issue_applies(found, {"iris": facts(12), "sodium": facts(5)}, "1.21.1", "fabric")
    assert not issue_applies(found, {"iris": facts(12)}, "1.21.1", "fabric")
    assert not issue_applies(found, {"iris": facts(8), "sodium": facts(5)}, "1.21.1", "fabric")


def test_a_broken_version_needs_no_second_mod() -> None:
    found = issue(IssueSide(project_id="iris", title="Iris", broken_from=version("1.7.2", 10), fixed_in=version("1.7.3", 11)))
    assert issue_applies(found, {"iris": facts(10)}, "1.21.1", "fabric")
    assert not issue_applies(found, {"iris": facts(11)}, "1.21.1", "fabric")


def test_the_loader_is_the_versions_not_the_packs() -> None:
    """A Fabric mod on a NeoForge pack through Connector: an issue seen on Fabric still applies."""
    found = issue(IssueSide(project_id="iris", title="Iris"), loader="fabric")
    assert issue_applies(found, {"iris": facts(10, loader="fabric")}, "1.21.1", "neoforge")
    assert not issue_applies(found, {"iris": facts(10, loader="quilt")}, "1.21.1", "quilt")


def test_minecraft_of_the_issue_must_match_when_set() -> None:
    found = issue(IssueSide(project_id="iris", title="Iris"), game_version="1.21.1")
    assert issue_applies(found, {"iris": facts(10)}, "1.21.1", "fabric")
    assert not issue_applies(found, {"iris": facts(10, mc="1.20.1")}, "1.20.1", "fabric")


def test_a_fix_on_another_branch_keeps_warning_where_it_broke() -> None:
    """Review 1: «Arreglado en» is set to a 1.21 version of an entry with no start; a 1.20.1 version, where it
    broke and no fix is known, still falls inside."""
    side = IssueSide(project_id="iris", title="Iris", fixed_in=version("1.8.0", 20, mc="1.21.1"))
    assert side_state(side, facts(25, mc="1.20.1")) == "in"
    assert side_state(side, facts(25)) == "out" and side_state(side, facts(15)) == "in"
