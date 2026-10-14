"""The panel with secondary loaders (batch C; spec 2026-10-13-loaders-secundarios-design.md §4):
the search tabs, the dialog that adds a bridged mod with what enters with it, and the actions of
the bridges' problems. The services are doubles, as in `tests/test_web_modpacks.py`."""
from typing import Any

from src.model.utils import MinecraftLoaders
from tests.test_web_modpacks import BUNDLE_ID, HTMX, FakeModpack, said, setup


def bridged(modpack: FakeModpack, loader: MinecraftLoaders = MinecraftLoaders.neoforge,
            bridges: tuple[str, ...] = ("connector",)) -> None:
    modpack.bundle = modpack.bundle.model_copy(update={"loader": loader, "secondary_loaders": list(bridges)})


def fabric_hits() -> list[dict[str, Any]]:
    return [
        {"project_id": "FM", "title": "FabricMiniMap", "project_type": "mod", "categories": ["fabric", "utility"],
         "author": "a", "downloads": 10},
        # Also for NeoForge: it is native, never offered through the bridge.
        {"project_id": "BOTH", "title": "Both", "project_type": "mod", "categories": ["fabric", "neoforge"],
         "author": "b", "downloads": 10},
        # A shader is in the native tab already.
        {"project_id": "SH", "title": "Shader", "project_type": "shader", "categories": ["iris"],
         "author": "c", "downloads": 10},
    ]


def search(client: Any, **params: str) -> str:
    response = client.get(f"/modpacks/{BUNDLE_ID}/search", params=params, headers=HTMX)
    assert response.status_code == 200
    return str(response.text)


# Task 5: the search tabs.

def test_without_a_bridge_the_search_has_no_tabs_and_asks_as_today() -> None:
    client, modpack, modrinth = setup()
    modpack.bundle = modpack.bundle.model_copy(update={"loader": MinecraftLoaders.neoforge})
    text = search(client, q="map")
    assert modrinth.searches == [("map", "1.21.1", "neoforge", 10)]
    assert "data-search-tab" not in text and "Vía Connector" not in text and "vía Connector" not in text


def test_with_an_active_bridge_the_native_tab_is_the_default_and_the_bridged_one_waits_for_a_click() -> None:
    client, modpack, modrinth = setup()
    bridged(modpack)
    text = search(client, q="map")
    assert modrinth.searches == [("map", "1.21.1", "neoforge", 10)]  # one request: the bridged count waits
    native = text[text.index('data-search-tab="nativos"'):].split(">")[0]
    assert 'aria-selected="true"' in native
    tab = text[text.rindex("<button", 0, text.index('data-search-tab="puente"')):]
    tab = tab[:tab.index("</button>")]
    assert f'hx-get="/modpacks/{BUNDLE_ID}/search?q=map&amp;via=connector"' in tab and 'hx-target="#search-results"' in tab
    assert said(tab) == "Vía Connector"  # no count before it is opened
    assert "vía Connector" not in text


def test_the_bridged_tab_searches_the_guest_loader_and_labels_each_bridged_hit() -> None:
    client, modpack, modrinth = setup()
    bridged(modpack)
    modrinth.hits = fabric_hits()
    text = search(client, q="map", via="connector")
    assert modrinth.searches == [("map", "1.21.1", "fabric", 10)]
    shown = said(text)
    assert "FabricMiniMap" in shown and "vía Connector (Fabric)" in shown
    assert "Both" not in shown and "Shader" not in shown
    assert "Vía Connector (1)" in shown
    tab = text[text.index('data-search-tab="puente"'):].split(">")[0]
    assert 'aria-selected="true"' in tab
    native = text[text.rindex("<button", 0, text.index('data-search-tab="nativos"')):].split(">")[0]
    assert f'hx-get="/modpacks/{BUNDLE_ID}/search?q=map"' in native
    # The hit adds as any other: the add route decides whether a dialog asks first.
    assert 'id="hit-add-FM"' in text and 'name="ref" value="FM"' in text


def test_kilt_is_labelled_experimental_and_searches_forge() -> None:
    client, modpack, modrinth = setup()
    bridged(modpack, MinecraftLoaders.fabric, ("kilt",))
    assert "Vía Kilt · experimental" in said(search(client, q="x"))
    modrinth.hits = [{"project_id": "FG", "title": "ForgeOnly", "project_type": "mod", "categories": ["forge"]}]
    text = said(search(client, q="x", via="kilt"))
    assert modrinth.searches[-1] == ("x", "1.21.1", "forge", 10)
    assert "vía Kilt (Forge) · experimental" in text


def test_a_bridge_that_is_not_active_is_searched_natively() -> None:
    client, modpack, modrinth = setup()  # Fabric, no bridge on
    text = search(client, q="x", via="kilt")
    assert modrinth.searches == [("x", "1.21.1", "fabric", 10)] and "data-search-tab" not in text


def test_modrinth_failing_in_the_bridged_tab_keeps_the_tabs_and_says_so() -> None:
    client, modpack, modrinth = setup()
    bridged(modpack)
    modrinth.error = RuntimeError("down")
    text = search(client, q="map", via="connector")
    assert "Modrinth no respondió" in text and 'data-search-tab="nativos"' in text
