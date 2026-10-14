"""
The bridges that let a pack run mods of another loader (batch C; d-7985ee-45c2ba, d-7985ee-171417).
Modrinth marks no bridge and its project lists are unions, so the table is kept by hand from each
bridge's version list (docs/research/2026-10-13-loaders-secundarios.md): a new Minecraft version is
added here after looking at it. Pure.
"""
from dataclasses import dataclass, field
from typing import Optional

CONNECTOR = "u58R1TMW"
FORGIFIED_FABRIC_API = "Aqlf1Shp"
LAUNCHPAD = "voWgQoWV"
KILT = "t87XZEqv"
FABRIC_API = "P7dR8mSH"
FABRIC_LANGUAGE_KOTLIN = "Ha28R6CL"


@dataclass(frozen=True)
class Bridge:
    key: str
    label: str
    # The pack's loader, and the loader of the mods the bridge runs on it.
    host: str
    guest: str
    game_versions: frozenset[str]
    # (Modrinth project id, the least stable channel accepted for it): the bridge and its companions.
    mods: tuple[tuple[str, str], ...]
    experimental: bool = False
    # Companions only some Minecraft versions need.
    extra: dict[str, tuple[tuple[str, str], ...]] = field(default_factory=dict)


BRIDGES: tuple[Bridge, ...] = (
    Bridge("connector", "Connector", "neoforge", "fabric", frozenset({"1.21", "1.21.1", "26.1.2"}),
           ((CONNECTOR, "beta"), (FORGIFIED_FABRIC_API, "release")),
           extra={"26.1.2": ((LAUNCHPAD, "release"),)}),
    Bridge("connector", "Connector", "forge", "fabric", frozenset({"1.20.1"}),
           ((CONNECTOR, "beta"), (FORGIFIED_FABRIC_API, "release"))),
    Bridge("kilt", "Kilt", "fabric", "forge", frozenset({"1.20.1", "1.21.1"}),
           ((KILT, "release"), (FABRIC_API, "release"), (FABRIC_LANGUAGE_KOTLIN, "release")),
           experimental=True),
)

# Why a change of a mod's way asks first (d-7985ee-7e602f): said by the dialog and by /v1's 409.
VIA_RISK = ("Cambiar de vía no siempre funciona: la configuración o los datos del mundo pueden no pasar. "
            "Simula en un servidor antes de instalar.")

# With Connector a dependency on Fabric API is Forgified Fabric API: the two never run together
# (Connector's README). Only this one (d-7985ee-d7fcae): nothing else is guessed.
REPLACES: dict[str, dict[str, str]] = {"connector": {FABRIC_API: FORGIFIED_FABRIC_API}}


def bridges_for(loader: str, game_version: str) -> list[Bridge]:
    """The bridges a pack with this loader and Minecraft version can turn on."""
    return [b for b in BRIDGES if b.host == loader and game_version in b.game_versions]


def bridge(key: str, loader: str, game_version: str) -> Optional[Bridge]:
    """The bridge `key` for this loader and version, or None when it does not apply."""
    return next((b for b in bridges_for(loader, game_version) if b.key == key), None)


def companions(found: Bridge, game_version: str) -> list[tuple[str, str]]:
    """The bridge's mods for this Minecraft version, with the channel each accepts."""
    return [*found.mods, *found.extra.get(game_version, ())]


def guest_of(key: str, host: str) -> Optional[str]:
    """The loader a bridge runs on a host loader, whatever the Minecraft version: how a mod already
    installed through it keeps being checked for updates."""
    return next((b.guest for b in BRIDGES if b.key == key and b.host == host), None)
