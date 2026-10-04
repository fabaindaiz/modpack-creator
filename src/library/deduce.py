"""Deduce a server's loader and Minecraft version from the jars Modrinth identified on it.

Pure: no network, no database. Each identified jar votes for the loaders and game versions of its
version; the strict leader wins, and a tie decides nothing (the admin picks).
"""
from collections import Counter
from dataclasses import dataclass
from typing import Optional

from src.model.utils import MinecraftLoaders

MINECRAFT_LOADERS = frozenset(loader.value for loader in MinecraftLoaders)


@dataclass(frozen=True)
class Deduction:
    loader: Optional[str]
    game_version: Optional[str]
    identified: int
    loader_votes: dict[str, int]
    version_votes: dict[str, int]


def _leader(votes: Counter[str]) -> Optional[str]:
    """The value with strictly more votes than the second, else None."""
    ranked = votes.most_common(2)
    if not ranked or (len(ranked) > 1 and ranked[0][1] == ranked[1][1]):
        return None
    return ranked[0][0]


def deduce_target(identified: list[tuple[list[str], list[str]]]) -> Deduction:
    """
    :param identified: One (loaders, game_versions) pair per identified jar.
    """
    loaders: Counter[str] = Counter()
    versions: Counter[str] = Counter()
    for jar_loaders, jar_versions in identified:
        loaders.update(set(jar_loaders) & MINECRAFT_LOADERS)
        versions.update(set(jar_versions))
    return Deduction(_leader(loaders), _leader(versions), len(identified), dict(loaders), dict(versions))
