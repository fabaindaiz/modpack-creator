"""Deducir el loader y la versión de un servidor a partir de sus jars identificados."""
from src.library.deduce import Deduction, deduce_target


def test_majority_loader_and_version() -> None:
    d = deduce_target([(["fabric"], ["1.21.1"])] * 8 + [(["quilt"], ["1.21.1"])])
    assert (d.loader, d.game_version, d.identified) == ("fabric", "1.21.1", 9)
    assert d.loader_votes == {"fabric": 8, "quilt": 1}


def test_a_tie_decides_nothing() -> None:
    d = deduce_target([(["fabric"], ["1.21.1"]), (["quilt"], ["1.21"])])
    assert d.loader is None and d.game_version is None


def test_only_minecraft_loaders_vote() -> None:
    d = deduce_target([(["iris", "fabric"], ["1.21.1"]), (["datapack"], ["1.21.1"])])
    assert d.loader == "fabric" and "iris" not in d.loader_votes and "datapack" not in d.loader_votes


def test_nothing_identified() -> None:
    assert deduce_target([]) == Deduction(None, None, 0, {}, {})
