"""What the visual sweep (scripts/visual_sweep.py) seeds and shoots, checked without a browser or a
database: the sweep itself runs by hand (scripts/visual-sweep.sh). The token migration
(d-7985ee-cc655d) asked it to also show «Cómo funciona», an open «?», a card's pin origins and a full
scan's reason; this keeps those in the sweep, with data that makes each of them appear."""
from typing import Any

import scripts.visual_sweep as sweep
from src.library.pins import pin_view
from src.library.scan import full_reason_text
from src.model.modpack import ModpackBundle
from src.model.utils import MinecraftLoaders

IDS = {key: f"id-{key}" for key in ("server", "server_new", "bundle", "pending", "empty", "job", "dryrun", "problems",
                                    "fix", "import_job", "downgrade", "bridged", "issue", "suspicion")}


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


def test_the_sweep_visits_the_knowledge_base() -> None:
    """O8 tanda 3: the base, a confirmed entry and a suspicion are shot whole."""
    assert {"knowledge", "knowledge-entry", "knowledge-suspicion"} <= set(sweep.pages(IDS))


class Clicked(BaseException):  # past the check's own `except Exception`
    pass


class RecordingPage:
    """Enough of a Playwright page for check_mod_drawer to reach its first click: what it waited for, in order."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def goto(self, url: str) -> None:
        self.calls.append("goto")

    def wait_for_load_state(self, state: str = "load") -> None:
        self.calls.append(f"load:{state}")

    def wait_for_function(self, expression: str, timeout: float = 0) -> None:
        self.calls.append(f"function:{expression}")

    def wait_for_timeout(self, ms: float) -> None:
        self.calls.append("timeout")

    def locator(self, selector: str) -> "RecordingPage":
        return self

    @property
    def context(self) -> "RecordingPage":
        return self

    def expect_page(self, timeout: float = 0) -> Any:
        from contextlib import nullcontext
        return nullcontext()

    def click(self, **options: object) -> None:
        self.calls.append("click")
        raise Clicked


def test_the_drawer_check_clicks_only_once_the_packs_jobs_ended() -> None:
    """The Ctrl+click of the mod drawer failed in 2 of 4 sweeps, at random widths: «Todos» right after the tabs
    (which resolve the pack and may queue its catalog job) is redrawn when that job ends, and its banner leaves,
    moving the link under the pointer. The check waits for no job in the banner, and for the redraw, first."""
    import pytest
    page = RecordingPage()
    with pytest.raises(Clicked):
        sweep.check_mod_drawer(page, "https://x", None, IDS, "p", [])  # type: ignore[arg-type]
    first_click = page.calls.index("click")
    waited = page.calls[:first_click]
    quiet = next(i for i, c in enumerate(waited) if c.startswith("function:") and "#job-banner" in c)
    assert "load:networkidle" in waited[quiet:]
