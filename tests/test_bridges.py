"""The bridges between loaders (batch C; d-7985ee-45c2ba, d-7985ee-171417): which a pack can turn on
for its loader and Minecraft version, and what each brings with it. From Modrinth's version lists,
read per version (the project's own list is a union): docs/research/2026-10-13-loaders-secundarios.md."""
from src.library.bridges import REPLACES, bridge, bridges_for, companions


def keys(loader: str, game_version: str) -> list[str]:
    return [b.key for b in bridges_for(loader, game_version)]


def test_connector_runs_fabric_mods_on_neoforge_and_on_forge_only_in_1_20_1() -> None:
    for version in ("1.21", "1.21.1", "26.1.2"):
        assert keys("neoforge", version) == ["connector"], version
    assert keys("forge", "1.20.1") == ["connector"]
    assert keys("forge", "1.21.1") == [] and keys("neoforge", "1.20.1") == []
    found = bridge("connector", "neoforge", "1.21.1")
    assert found is not None and found.guest == "fabric" and not found.experimental


def test_kilt_runs_forge_mods_on_fabric_and_says_it_is_experimental() -> None:
    assert keys("fabric", "1.20.1") == ["kilt"] and keys("fabric", "1.21.1") == ["kilt"]
    found = bridge("kilt", "fabric", "1.21.1")
    assert found is not None and found.guest == "forge" and found.experimental


def test_quilt_and_other_loaders_have_no_bridge() -> None:
    assert keys("quilt", "1.21.1") == [] and keys("fabric", "1.21") == []


def test_what_each_bridge_brings_by_version() -> None:
    connector = bridge("connector", "neoforge", "26.1.2")
    assert connector is not None
    assert companions(connector, "26.1.2") == [("u58R1TMW", "beta"), ("Aqlf1Shp", "release"), ("voWgQoWV", "release")]
    older = bridge("connector", "neoforge", "1.21.1")
    assert older is not None and companions(older, "1.21.1") == [("u58R1TMW", "beta"), ("Aqlf1Shp", "release")]
    kilt = bridge("kilt", "fabric", "1.20.1")
    assert kilt is not None
    assert companions(kilt, "1.20.1") == [("t87XZEqv", "release"), ("P7dR8mSH", "release"), ("Ha28R6CL", "release")]


def test_connector_resolves_fabric_api_as_forgified_fabric_api() -> None:
    assert REPLACES == {"connector": {"P7dR8mSH": "Aqlf1Shp"}}
