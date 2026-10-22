"""What the visual sweep (scripts/visual_sweep.py) seeds and shoots, checked without a browser or a
database: the sweep itself runs by hand (scripts/visual-sweep.sh). The token migration
(d-7985ee-cc655d) asked it to also show «Cómo funciona», an open «?», a card's pin origins and a full
scan's reason; this keeps those in the sweep, with data that makes each of them appear."""
import scripts.visual_sweep as sweep
from src.library.pins import pin_view
from src.library.scan import full_reason_text
from src.model.modpack import ModpackBundle
from src.model.utils import MinecraftLoaders

IDS = {key: f"id-{key}" for key in ("server", "server_new", "bundle", "pending", "empty", "job", "dryrun", "problems",
                                    "fix", "import_job", "downgrade", "bridged")}


def test_the_sweep_visits_como_funciona() -> None:
    assert sweep.pages(IDS)["help"] == "/como-funciona"


def test_the_sweep_shoots_an_open_help_the_pin_origins_and_the_scan_reason() -> None:
    assert {"help-open", "pin-origins", "server-scan-reason"} <= set(sweep.TOKEN_SHOTS)


def test_the_seeded_full_scan_has_a_reason_the_server_page_says() -> None:
    assert full_reason_text(sweep.SCAN_FULL_REASON)


def test_the_seeded_pin_has_several_origins_on_its_card() -> None:
    record = sweep.pin_origins_record()
    bundle = ModpackBundle.model_construct(name="x", description="", version="26.3", loader=MinecraftLoaders.fabric,
                                           pins={sweep.PIN_ORIGINS_MOD: sweep.PIN_ORIGINS_VERSION},
                                           pin_origins={sweep.PIN_ORIGINS_MOD: record})
    view = pin_view(bundle, sweep.PIN_ORIGINS_MOD)
    assert view is not None and len(view.origins) >= 2
    assert view.selected == record.selected  # the pinned version is the origin in use, not «elegida a mano»
    assert any(o.valid is False and o.problem for o in view.origins)  # a line that says why it does not pass
