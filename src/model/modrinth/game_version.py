from datetime import datetime
from pydantic import BaseModel
from typing import Iterable

class ModrinthGameVersion(BaseModel):
    """One entry of Modrinth's /tag/game_version, which lists them newest first."""
    version: str
    version_type: str
    date: datetime
    major: bool

def newest_first(versions: Iterable[str], tags: Iterable[ModrinthGameVersion]) -> list[str]:
    """
    Order game versions newest first, as Modrinth orders them.

    Never by parsing them: Minecraft numbers by year since 2026 (26.3 follows 1.21.11), and as
    text 1.21.9 sorts after 1.21.10. A version Modrinth does not list is an error, not a guess.

    :param versions: The game versions to order.
    :param tags: Modrinth's game version tags, in the order the API returns them.
    :return: The versions, newest first.
    """
    position = {tag.version: index for index, tag in enumerate(tags)}
    versions = list(versions)
    unknown = [v for v in versions if v not in position]
    if unknown:
        raise ValueError(f"Game versions Modrinth does not list: {', '.join(unknown)}")
    return sorted(versions, key=position.__getitem__)
