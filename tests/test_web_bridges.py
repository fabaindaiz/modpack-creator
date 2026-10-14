"""The panel with secondary loaders (batch C; spec 2026-10-13-loaders-secundarios-design.md §4):
the search tabs, the dialog that adds a bridged mod with what enters with it, and the actions of
the bridges' problems. The services are doubles, as in `tests/test_web_modpacks.py`."""
import re
from typing import Any

from src.library.bridges import CONNECTOR, FABRIC_API, FORGIFIED_FABRIC_API
from src.library.resolution import Problem
from src.model.modpack import PackProject, ProjectOrigin
from src.model.utils import MinecraftLoaders
from tests._actors import EDITOR
from tests.test_web_modpacks import (BUNDLE_ID, HTMX, FakeModpack, call, entry, lock, problem_card, problems_page,
                                     resolution, said, setup)


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


# Task 6: adding a bridged mod asks first (C4, d-7985ee-56a529).

MISSING = [(CONNECTOR, "Connector"), (FORGIFIED_FABRIC_API, "Forgified Fabric API")]


def dialog_form(text: str) -> str:
    start = text.index("<form", text.index("<dialog"))
    return text[start:text.index("</form>", start)]


def test_the_pack_page_has_the_bridge_dialog_slot() -> None:
    client, _, _ = setup()
    assert '<div id="bridge-dialog"></div>' in client.get(f"/modpacks/{BUNDLE_ID}").text


def test_adding_a_bridged_mod_answers_a_dialog_with_what_enters_and_adds_nothing() -> None:
    client, modpack, modrinth = setup()
    bridged(modpack)
    modpack.missing = MISSING  # type: ignore[attr-defined]
    modrinth.titles["FM"] = "FabricMiniMap"
    response = client.post(f"/modpacks/{BUNDLE_ID}/projects", headers={**HTMX, "HX-Trigger": "hit-add-FM"},
                           data={"ref": "FM"})
    assert response.status_code == 200
    assert response.headers["HX-Retarget"] == "#bridge-dialog" and response.headers["HX-Reswap"] == "innerHTML"
    assert not [c for c in modpack.calls if c[0] == "project_add"]
    text = response.text
    assert "<dialog data-autoopen" in text
    assert "FabricMiniMap necesita Connector y Forgified Fabric API (en lugar de Fabric API)" in said(text)
    assert "vía Connector (Fabric)" in said(text)
    form = dialog_form(text)
    assert f'hx-post="/modpacks/{BUNDLE_ID}/projects"' in form and 'hx-target="#mods"' in form
    assert 'id="bridge-add-FM"' in form and 'name="confirm_bridge" value="1"' in form
    refs = [part.split('"')[0] for part in form.split('name="ref" value="')[1:]]
    assert refs == ["FM", CONNECTOR, FORGIFIED_FABRIC_API]
    assert "Cancelar" in said(text) and "Agregar todo" in said(form) and "data-dialog-close" in text


def test_agregar_todo_adds_the_mod_and_its_companions_and_answers_the_mods() -> None:
    client, modpack, _ = setup()
    bridged(modpack)
    modpack.missing = MISSING  # type: ignore[attr-defined]
    response = client.post(f"/modpacks/{BUNDLE_ID}/projects", headers={**HTMX, "HX-Trigger": "bridge-add-FM"},
                           data={"ref": ["FM", CONNECTOR, FORGIFIED_FABRIC_API], "confirm_bridge": "1"})
    assert response.status_code == 200 and 'id="mods"' in response.text and "HX-Retarget" not in response.headers
    assert call(modpack, "project_add")[1:] == (str(BUNDLE_ID), ["FM", CONNECTOR, FORGIFIED_FABRIC_API],
                                               ProjectOrigin.admin, EDITOR)
    assert re.search(r'<div id="hit-FM" hx-swap-oob="true"', response.text)  # the search hit says «Ya está»


def test_a_mod_that_needs_nothing_more_is_added_as_always() -> None:
    client, modpack, _ = setup()
    bridged(modpack)
    response = client.post(f"/modpacks/{BUNDLE_ID}/projects", headers=HTMX, data={"ref": "FM"})
    assert response.status_code == 200 and "HX-Retarget" not in response.headers
    assert call(modpack, "project_add")[2] == ["FM"]


def test_several_mods_are_added_together_only_once_confirmed() -> None:
    client, modpack, _ = setup()
    bridged(modpack)
    response = client.post(f"/modpacks/{BUNDLE_ID}/projects", headers=HTMX, data={"ref": ["A", "FM"]})
    assert response.status_code == 422 and not [c for c in modpack.calls if c[0] == "project_add"]


def test_agregar_todo_from_a_problem_card_resolves_and_answers_there() -> None:
    client, modpack, _ = setup()
    bridged(modpack)
    response = client.post(f"/modpacks/{BUNDLE_ID}/projects", headers=HTMX,
                           data={"ref": [CONNECTOR, FORGIFIED_FABRIC_API], "confirm_bridge": "1", "contexto": "problemas"})
    assert response.status_code == 200 and 'data-vista="problemas"' in response.text
    assert [c[0] for c in modpack.calls if c[0] in ("project_add", "resolve")] == ["project_add", "resolve"]


def test_the_dialog_from_a_problem_card_sends_its_context_back() -> None:
    client, modpack, _ = setup()
    bridged(modpack)
    modpack.missing = [(FORGIFIED_FABRIC_API, "Forgified Fabric API")]  # type: ignore[attr-defined]
    response = client.post(f"/modpacks/{BUNDLE_ID}/projects", headers=HTMX,
                           data={"ref": CONNECTOR, "contexto": "dialogo"})
    form = dialog_form(response.text)
    assert 'name="contexto" value="dialogo"' in form and 'hx-target="#build-dialog"' in form


def test_the_problems_of_a_bridge_have_their_actions() -> None:
    client, modpack, _ = setup()
    bridged(modpack)
    modpack.bundle = modpack.bundle.model_copy(update={"projects": [
        PackProject(project_id="P1", origin=ProjectOrigin.pack), PackProject(project_id=FABRIC_API, origin=ProjectOrigin.admin)]})
    modpack.titles |= {FABRIC_API: "Fabric API", CONNECTOR: "Connector", "DEP": "FabricLib"}
    text = problems_page(modpack, client,
                         Problem(kind="bridge_missing", project_id=CONNECTOR, detail=f"Connector necesita {CONNECTOR}"),
                         Problem(kind="bridge_dependency", project_id="DEP", requested_by=["P1"], detail="DEP solo existe para fabric"),
                         Problem(kind="bridge_conflict", project_id=FABRIC_API, detail="Fabric API no convive con Forgified Fabric API"),
                         Problem(kind="via_change", project_id="P1", detail="P1 pasó a nativa"))
    missing = problem_card(text, CONNECTOR)
    assert "falta el puente" in said(missing) and "Agregar el puente" in said(missing)
    assert f'hx-post="/modpacks/{BUNDLE_ID}/projects"' in missing and f'name="ref" value="{CONNECTOR}"' in missing
    assert 'name="contexto" value="problemas"' in missing and "confirm_bridge" not in missing  # the dialog asks
    dependency = problem_card(text, "DEP")
    assert "solo por el puente" in said(dependency) and "Usar vía Connector" in said(dependency)
    assert 'name="ref" value="DEP"' in dependency and "confirm_bridge" not in dependency
    conflict = problem_card(text, FABRIC_API)
    assert "choca con el puente" in said(conflict) and "Quitar Fabric API" in said(conflict)
    assert f'hx-delete="/modpacks/{BUNDLE_ID}/projects/{FABRIC_API}?contexto=problemas"' in conflict
    change = problem_card(text, "P1").split("</article>")[0]
    assert "cambio de vía" in said(change) and "<form" not in change and "hx-delete" not in change


# The review of batch C, minors 1, 2 and 5.


def test_a_mod_whose_bridge_is_off_offers_turning_it_back_on_or_removing_it() -> None:
    """Minor 2 (spec, errors): the card turns the bridge back on, from where it was clicked, or
    removes the mod; nothing happens until it is clicked."""
    client, modpack, _ = setup()
    bridged(modpack, bridges=())
    text = problems_page(modpack, client, Problem(kind="via_change", project_id="P1", bridge="connector",
                                                  detail="P1 está instalado vía Connector, que el pack apagó"))
    card = problem_card(text, "P1")
    assert "Reactivar el puente" in said(card) and f'hx-post="/modpacks/{BUNDLE_ID}/bridges"' in card
    assert 'name="bridge" value="connector"' in card and 'name="contexto" value="problemas"' in card
    assert f'hx-delete="/modpacks/{BUNDLE_ID}/projects/P1?contexto=problemas"' in card


def test_turning_a_bridge_back_on_from_a_card_resolves_and_answers_there() -> None:
    client, modpack, _ = setup()
    bridged(modpack, bridges=())
    response = client.post(f"/modpacks/{BUNDLE_ID}/bridges", headers=HTMX,
                           data={"bridge": "connector", "contexto": "problemas"})
    assert response.status_code == 200 and response.text.lstrip().startswith('<div id="mods"')
    assert call(modpack, "bundle_update")[2].secondary_loaders == ["connector"]
    assert [c[0] for c in modpack.calls].count("resolve") == 1


def test_saving_the_bridges_resolves_again_and_redraws_the_mods() -> None:
    """Minor 5 (spec §4: «Guardarla resuelve de nuevo»): the section answers, #mods comes out of band."""
    client, modpack, _ = setup()
    bridged(modpack)
    response = client.post(f"/modpacks/{BUNDLE_ID}/bridges", headers=HTMX, data={"bridge": "connector"})
    assert response.status_code == 200 and response.text.lstrip().startswith('<form id="bridges"')
    assert '<div id="mods" class="space-y-4" hx-swap-oob="true"' in response.text
    assert [c[0] for c in modpack.calls].count("resolve") == 1


def test_volver_al_puente_only_while_the_installed_version_still_runs_through_it() -> None:
    """Minor 1: once the build in force has the native version, the tab says so and offers nothing."""
    client, modpack, _ = setup()
    bridged(modpack)
    modpack.bundle = modpack.bundle.model_copy(update={"native_only": ["P1", "P2"]})
    modpack.via_back = {"P1"}
    text = client.get(f"/modpacks/{BUNDLE_ID}", params={"vista": "puente"}).text
    back = text.split('data-project="P1"')[1].split("</li>")[0]
    built = text.split('data-project="P2"')[1].split("</li>")[0]
    assert "Volver al puente" in said(back) and 'name="native" value="0"' in back
    assert "ya instalada nativa" in said(built) and "<form" not in built
