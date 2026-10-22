"""Los orígenes de una fijación en la tarjeta de un mod (O2; docs/research/2026-10-21-observaciones-ronda-3.md).

La tarjeta muestra de dónde sale cada versión candidata (versión del pack, elegida a mano,
resolución), si pasa la validación del pack y cuál está en uso, y deja poner otra en uso; una baja
abre el mismo diálogo de confirmación que un pin. El servicio es el doble de `test_web_modpacks`.
"""
import re

from src.model.modpack import PinSource
from src.services.modpack import OriginRow
from tests._actors import EDITOR
from tests.test_web_modpacks import BUNDLE_ID, HTMX, JEI_DOWN, call, card, row, setup, visible


def origins() -> list[OriginRow]:
    return [OriginRow(source="pack", label="versión del pack", version_id="mt-pack", version_number="1.0.4",
                      valid=False, problem="Modrinth no la marca para 1.21.1", named_by="Aventura 2.0", in_use=True),
            OriginRow(source="resolution", label="resolución", version_id="mt-2", version_number="1.1.0",
                      valid=True, problem=None, named_by=None, in_use=False)]


def test_the_card_lists_the_origins_marks_the_one_in_use_and_why_one_does_not_pass() -> None:
    client, modpack, _ = setup()
    modpack.rows = [row("P1", "MixinTrace Reborn", pinned=True, pinned_version="mt-pack", pin_origins=origins(),
                        off_tags=True), row("P2", "Sodium")]
    text = card(client.get(f"/modpacks/{BUNDLE_ID}").text, "P1")
    shown = " ".join(visible(text).split())
    assert "versión del pack, fuera de las etiquetas de Modrinth" in shown
    assert "De dónde sale la versión" in shown
    assert "versión del pack 1.0.4 (Aventura 2.0) en uso" in shown
    assert "Modrinth no la marca para 1.21.1" in shown
    assert "resolución 1.1.0" in shown and "pasa la validación del pack" in shown
    # Only the origin not in use offers itself, posted to the origin route.
    forms = re.findall(r'<form[^>]*hx-post="([^"]*/origen)"[^>]*>.*?</form>', text, re.S)
    assert forms == [f"/modpacks/{BUNDLE_ID}/pins/P1/origen"]
    assert 'name="origin" value="resolution"' in text and "Usar esta" in shown


def test_a_servers_origin_is_named_after_the_server_and_offered() -> None:
    """Owner, 2026-10-21: a scanned linked server's version reads «servidor: Survival», never in use alone."""
    client, modpack, _ = setup()
    server = OriginRow(source="server", label="servidor", version_id="mt-3", version_number="1.2.0", valid=True,
                       problem=None, named_by="Survival", in_use=False)
    modpack.rows = [row("P1", "MixinTrace Reborn", pinned=True, pinned_version="mt-pack",
                        pin_origins=[*origins(), server]), row("P2", "Sodium")]
    text = card(client.get(f"/modpacks/{BUNDLE_ID}").text, "P1")
    shown = " ".join(visible(text).split())
    assert "servidor: Survival 1.2.0" in shown and "(Survival)" not in shown
    assert 'name="origin" value="server"' in text


def test_a_mod_without_origins_shows_no_origin_list() -> None:
    client, modpack, _ = setup()
    modpack.rows = [row("P1", "Sodium", pinned=True, pinned_version="ver-p1"), row("P2", "Lithium")]
    shown = visible(card(client.get(f"/modpacks/{BUNDLE_ID}").text, "P1"))
    assert "De dónde sale la versión" not in shown and "fuera de las etiquetas" not in shown


def test_choosing_an_origin_calls_the_service_with_the_actor_and_redraws_the_mods() -> None:
    client, modpack, _ = setup()
    response = client.post(f"/modpacks/{BUNDLE_ID}/pins/P1/origen", data={"origin": "resolution"}, headers=HTMX)
    assert response.status_code == 200 and 'id="mods"' in response.text
    assert call(modpack, "pin_origin_select") == ("pin_origin_select", str(BUNDLE_ID), "P1", PinSource.resolution, EDITOR)
    assert modpack.confirmed == [False]


def test_an_unknown_origin_is_refused_before_the_service() -> None:
    client, modpack, _ = setup()
    response = client.post(f"/modpacks/{BUNDLE_ID}/pins/P1/origen", data={"origin": "otro"}, headers=HTMX)
    assert response.status_code == 422 and not modpack.calls


def test_an_origin_that_goes_back_opens_the_downgrade_dialog_that_sends_it_again_confirmed() -> None:
    client, modpack, _ = setup()
    modpack.downgrade = JEI_DOWN
    response = client.post(f"/modpacks/{BUNDLE_ID}/pins/P2/origen", data={"origin": "pack"}, headers=HTMX)
    assert response.headers["HX-Retarget"] == "#pin-dialog"
    text = response.text
    assert "Baja de versión" in text and "JEI 19.57 → 19.51" in text and "Fijar igual" in text
    form = re.search(r"<form[^>]*>", text)
    assert form and f'hx-post="/modpacks/{BUNDLE_ID}/pins/P2/origen"' in form.group(0)
    assert 'name="origin" value="pack"' in text and 'name="confirm_downgrade" value="1"' in text
    client.post(f"/modpacks/{BUNDLE_ID}/pins/P2/origen", data={"origin": "pack", "confirm_downgrade": "1"}, headers=HTMX)
    assert modpack.confirmed == [False, True]
