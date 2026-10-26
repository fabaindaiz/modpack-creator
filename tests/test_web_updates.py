"""La página de actualizaciones (N6), el botón, "Crear build" y la insignia del tablero.

Los servicios se sustituyen con dobles, como en `tests/test_web_modpacks.py`.
"""
from datetime import datetime, timezone
from typing import Any, Optional

import pytest
from beanie import PydanticObjectId
from dependency_injector import providers
from fastapi.testclient import TestClient

from src.exceptions import HTTP_409_CONFLICT, BuildBlocked
from src.library.changes import Change, ChangeSet
from src.model.jobs import Job
from src.model.modpack import AvailableUpdate, ModpackBuild, ModpackBundle, UpdateCheck
from src.model.users import Actor
from src.model.utils import MinecraftLoaders
from src.services.modpack import ChangePreview, ModList
from tests._actors import EDITOR, session_for
from tests._session import session_cookie

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
BUNDLE_ID = PydanticObjectId()
SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}
HTMX = {"HX-Request": "true", **SAME_ORIGIN}


def update(project_id: str, title: str, pinned: bool = False) -> AvailableUpdate:
    return AvailableUpdate(project_id=project_id, title=title, from_version="1.0", to_version="2.0", pinned=pinned,
                           from_version_id=f"{project_id}-1", to_version_id=f"{project_id}-2")


def check(updates: list[AvailableUpdate], error: Optional[str] = None) -> UpdateCheck:
    return UpdateCheck.model_construct(id=PydanticObjectId(), bundle_id=BUNDLE_ID, build_number=2, checked_at=T0,
                                       updates=updates, error=error, notified_digest=None)


class FakeModpack:
    def __init__(self) -> None:
        self.bundle = ModpackBundle.model_construct(
            id=BUNDLE_ID, name="Pack Uno", description="", version="1.21.1", loader=MinecraftLoaders.fabric,
            latest_build=2, recommended_build=2, projects=[], pins={}, channels=["release"], server_decisions={},
            local_files=[])
        self.calls: list[tuple[Any, ...]] = []
        self.previewed: list[list[AvailableUpdate]] = []
        self.failed: set[str] = set()
        self.changelogs: dict[str, Optional[str]] = {}

    async def bundle_get(self, bundle_id: str) -> ModpackBundle:
        return self.bundle

    jobs: list[Any] = []

    async def active_jobs(self, bundle_id: str) -> list[Any]:
        return list(self.jobs)

    async def resolve_current(self, bundle_id: str, actor: Any = None) -> None:
        return None  # a pack the catalog lacks: it stays as it is (O9 D2)

    async def build_find(self, bundle_id: str) -> list[ModpackBuild]:
        return []

    async def current_resolutions(self, bundles: list[ModpackBundle]) -> dict[str, Any]:
        return {}

    async def build_request(self, bundle_id: str, actor: Actor) -> Any:
        self.calls.append(("build_request", bundle_id, actor))
        return None

    async def build_create(self, bundle_id: str, actor: Actor) -> ModpackBuild:
        self.calls.append(("build_create", bundle_id, actor))
        return ModpackBuild.model_construct(id=PydanticObjectId(), bundle_id=BUNDLE_ID, number=3, created_at=T0)

    async def update_preview(self, updates: list[AvailableUpdate]) -> ChangePreview:
        self.previewed.append(updates)
        changed = [Change(u.project_id, u.from_version_id, u.to_version_id) for u in updates]
        numbers = {i: n for u in updates for i, n in ((u.from_version_id, u.from_version), (u.to_version_id, u.to_version))}
        return ChangePreview(changes=ChangeSet(added=[], removed=[], changed=changed, unchanged=0),
                             changelogs={u.to_version_id: self.changelogs.get(u.to_version_id) for u in updates
                                         if u.to_version_id not in self.failed},
                             failed=set(self.failed), numbers=numbers)

    async def latest_builds(self) -> list[tuple[ModpackBundle, Optional[ModpackBuild]]]:
        return [(self.bundle, None)]

    async def mod_list(self, bundle_id: str, check: Optional[UpdateCheck], bundle: Any = None) -> ModList:
        return ModList(rows=[], resolution=None, stale=False, icons_missing=0)


class FakeChecks:
    def __init__(self) -> None:
        self.latest_check: Optional[UpdateCheck] = None
        self.calls: list[tuple[Any, ...]] = []
        self.many_calls: list[list[str]] = []
        self.busy = False

    async def latest(self, bundle_id: str) -> Optional[UpdateCheck]:
        return self.latest_check

    async def latest_many(self, bundle_ids: list[str]) -> dict[PydanticObjectId, UpdateCheck]:
        self.many_calls.append(bundle_ids)
        return {BUNDLE_ID: self.latest_check} if self.latest_check else {}

    async def enqueue(self, bundle_id: str, actor: Optional[Actor]) -> Job:
        self.calls.append(("enqueue", bundle_id, actor))
        if self.busy:
            raise HTTP_409_CONFLICT("Ya hay una revisión en curso para este modpack")
        return Job.model_construct(id=PydanticObjectId(), kind="update_check", key=f"bundle:{bundle_id}", status="queued",
                                   active=True, step=None, created_at=T0, attempts=0, max_attempts=3, error=None,
                                   requested_by=actor.ref() if actor else None, payload={"bundle_id": bundle_id})


class FakePtero:
    async def server_find(self) -> list[Any]:
        return []


class FakeModrinth:
    """The catalog fill of the page: counts its calls (one per render) and knows no project, so the
    titles are the check's."""

    def __init__(self) -> None:
        self.filled: list[list[str]] = []

    async def projects_filled(self, project_ids: list[str]) -> dict[str, Any]:
        self.filled.append(list(project_ids))
        return {}


class FakeJobs:
    async def recent(self, limit: int = 10) -> list[Job]:
        return []

    async def find(self, **filters: Any) -> list[Job]:
        return []


@pytest.fixture(autouse=True)
def reset_overrides() -> Any:
    yield
    import main

    for provider in (main.container.modpackService, main.container.updateCheckService, main.container.jobService,
                     main.container.pterodactylService, main.container.modrinthService):
        provider.reset_override()


def setup(actor: Optional[Actor] = EDITOR) -> tuple[TestClient, FakeModpack, FakeChecks]:
    client, modpack, checks, _ = setup_all(actor)
    return client, modpack, checks


def setup_all(actor: Optional[Actor] = EDITOR) -> tuple[TestClient, FakeModpack, FakeChecks, FakeModrinth]:
    import main

    modpack, checks, modrinth = FakeModpack(), FakeChecks(), FakeModrinth()
    main.container.modpackService.override(providers.Object(modpack))
    main.container.updateCheckService.override(providers.Object(checks))
    main.container.jobService.override(providers.Object(FakeJobs()))
    main.container.pterodactylService.override(providers.Object(FakePtero()))
    main.container.modrinthService.override(providers.Object(modrinth))
    client = TestClient(main.app, base_url="https://testserver", follow_redirects=False)
    if actor is not None:
        client.cookies.set("session", session_cookie(session_for(actor)))
    return client, modpack, checks, modrinth


def test_the_page_without_a_check_invites_to_search() -> None:
    client, modpack, checks = setup()
    response = client.get(f"/modpacks/{BUNDLE_ID}/actualizaciones")
    assert response.status_code == 200
    assert "Todavía no se ha revisado" in response.text
    assert f'hx-post="/modpacks/{BUNDLE_ID}/actualizaciones/buscar"' in response.text
    assert "Crear build" not in response.text and modpack.previewed == []


def test_updates_main_reloads_itself_when_the_check_ends_and_excludes_its_row() -> None:
    import re

    client, _, checks = setup()
    checks.latest_check = check([update("A", "Sodium")])
    text = client.get(f"/modpacks/{BUNDLE_ID}/actualizaciones").text
    match = re.search(r'<[a-z]+ [^>]*id="updates-main"[^>]*>', text)
    assert match
    tag = match.group(0)
    for attribute in (f'hx-get="/modpacks/{BUNDLE_ID}/actualizaciones"', 'hx-select="#updates-main"',
                      'hx-trigger="job-finished from:body"', 'hx-swap="outerHTML"', 'hx-disinherit="*"'):
        assert attribute in tag
    # The check's row polls in #check-job, which stays outside: the reload never restarts it.
    assert text.index('id="check-job"') < text.index(tag) < text.index("Sodium")
    assert 'hx-target="#check-job"' in text


def test_the_page_lists_updates_with_titles_pins_apart_and_the_resolution_note() -> None:
    client, modpack, checks = setup()
    checks.latest_check = check([update("A", "Sodium"), update("C", "Lithium", pinned=True)])
    modpack.changelogs["A-2"] = "Arregla <b>cosas</b>"
    text = client.get(f"/modpacks/{BUNDLE_ID}/actualizaciones").text
    assert "Sodium" in text and "1.0 → 2.0" in text
    assert "Arregla &lt;b&gt;cosas&lt;/b&gt;" in text and "<b>cosas</b>" not in text
    assert "Lithium" in text and "fijad" in text
    assert [u.project_id for u in modpack.previewed[0]] == ["A"]  # lo fijado no se previsualiza
    assert "Crear build" in text and "puede diferir" in text


def test_a_changelog_that_failed_says_so_and_the_page_is_still_200() -> None:
    client, modpack, checks = setup()
    checks.latest_check = check([update("A", "Sodium")])
    modpack.failed = {"A-2"}
    response = client.get(f"/modpacks/{BUNDLE_ID}/actualizaciones")
    assert response.status_code == 200 and "changelog no disponible" in response.text


def test_a_check_with_an_error_shows_it_and_no_list() -> None:
    client, modpack, checks = setup()
    checks.latest_check = check([], error="RuntimeError: Modrinth down")
    text = client.get(f"/modpacks/{BUNDLE_ID}/actualizaciones").text
    assert "RuntimeError: Modrinth down" in text and "Crear build" not in text
    assert modpack.previewed == []


def test_a_modpack_without_a_build_says_to_create_one_and_offers_no_search() -> None:
    # Tester feedback (2026-10-08): an imported pack without a build showed "build #0" and a raw 409.
    client, modpack, checks = setup()
    modpack.bundle.latest_build = modpack.bundle.recommended_build = None
    checks.latest_check = UpdateCheck.model_construct(
        id=PydanticObjectId(), bundle_id=BUNDLE_ID, build_number=0, checked_at=T0, updates=[],
        error="HTTP_409_CONFLICT: 409: Bundle 'x' has no build", notified_digest=None)
    text = client.get(f"/modpacks/{BUNDLE_ID}/actualizaciones").text
    assert "Este modpack todavía no tiene build: crea uno para poder buscar actualizaciones." in text
    assert "actualizaciones/buscar" not in text
    assert "#0" not in text and "HTTP_409" not in text


def test_a_stored_check_without_a_build_number_does_not_show_build_0() -> None:
    client, _, checks = setup()
    checks.latest_check = UpdateCheck.model_construct(
        id=PydanticObjectId(), bundle_id=BUNDLE_ID, build_number=0, checked_at=T0, updates=[],
        error="RuntimeError: Modrinth down", notified_digest=None)
    text = client.get(f"/modpacks/{BUNDLE_ID}/actualizaciones").text
    assert "Modrinth down" in text and "#0" not in text


def test_a_check_without_updates_says_everything_is_current() -> None:
    client, _, checks = setup()
    checks.latest_check = check([])
    text = client.get(f"/modpacks/{BUNDLE_ID}/actualizaciones").text
    assert "al día" in text and "Crear build" not in text


def test_the_button_enqueues_with_the_actor_and_answers_with_the_job_row() -> None:
    client, _, checks = setup()
    response = client.post(f"/modpacks/{BUNDLE_ID}/actualizaciones/buscar", headers=HTMX)
    assert response.status_code == 200
    assert checks.calls == [("enqueue", str(BUNDLE_ID), EDITOR)]
    assert 'hx-trigger="every 2s"' in response.text and "Revisión de actualizaciones" in response.text
    assert "Eddie Editor" in response.text


def test_a_busy_modpack_is_a_readable_conflict_notice() -> None:
    client, _, checks = setup()
    checks.busy = True
    response = client.post(f"/modpacks/{BUNDLE_ID}/actualizaciones/buscar", headers=HTMX)
    assert response.status_code == 409 and "Ya hay una revisión en curso para este modpack" in response.text


def test_create_build_queues_the_build_job_with_the_actor_and_goes_to_the_builds() -> None:
    client, modpack, _ = setup()
    response = client.post(f"/modpacks/{BUNDLE_ID}/actualizaciones/build", headers=HTMX)
    assert response.status_code == 200 and response.headers["HX-Redirect"] == f"/modpacks/{BUNDLE_ID}#builds-section"
    assert modpack.calls == [("build_request", str(BUNDLE_ID), EDITOR)]
    plain = client.post(f"/modpacks/{BUNDLE_ID}/actualizaciones/build", headers=SAME_ORIGIN)
    assert plain.status_code == 303 and plain.headers["location"] == f"/modpacks/{BUNDLE_ID}#builds-section"


def test_create_build_with_a_conflict_is_a_notice() -> None:
    client, modpack, _ = setup()

    async def refuse(bundle_id: str, actor: Actor) -> Any:
        raise HTTP_409_CONFLICT("Hay mods sin decidir")

    modpack.build_request = refuse  # type: ignore[method-assign]
    response = client.post(f"/modpacks/{BUNDLE_ID}/actualizaciones/build", headers=HTMX)
    assert response.status_code == 409 and "Hay mods sin decidir" in response.text


def test_a_blocked_build_goes_to_the_pack_with_its_dialog_open() -> None:
    """The same button on the pack opens the dialog with what is missing (O9 user-walk, A9): here it
    was a bare 409."""
    client, modpack, _ = setup()

    async def blocked(bundle_id: str, actor: Actor) -> Any:
        raise BuildBlocked("Hay mods sin decidir", lock=Any, only_undecided=True)  # type: ignore[arg-type]

    modpack.build_request = blocked  # type: ignore[method-assign]
    response = client.post(f"/modpacks/{BUNDLE_ID}/actualizaciones/build", headers=HTMX)
    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == f"/modpacks/{BUNDLE_ID}?construir=1#builds-section"
    page = client.get(f"/modpacks/{BUNDLE_ID}?construir=1").text
    dialog = page[page.index('id="build-dialog"'):]
    assert 'hx-trigger="load, build-blocked from:body"' in dialog[:dialog.index(">")]
    plain = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert 'hx-trigger="build-blocked from:body"' in plain


def test_the_detail_links_to_the_page() -> None:
    client, _, _ = setup()

    text = client.get(f"/modpacks/{BUNDLE_ID}").text
    assert f'hx-post="/modpacks/{BUNDLE_ID}/actualizaciones/buscar?abrir=1"' in text and "Buscar actualizaciones de mods" in text


def test_the_dashboard_badge_counts_without_the_pinned_ones_in_one_query() -> None:
    client, _, checks = setup()
    checks.latest_check = check([update("A", "Sodium"), update("B", "Iris"), update("C", "Lithium", pinned=True)])
    text = client.get("/").text
    assert "2 actualizaciones" in text and f'href="/modpacks/{BUNDLE_ID}/actualizaciones"' in text
    assert checks.many_calls == [[str(BUNDLE_ID)]]


def test_the_dashboard_has_no_badge_when_only_pinned_ones_or_no_check() -> None:
    client, _, checks = setup()
    assert "actualizacion" not in client.get("/").text
    checks.latest_check = check([update("C", "Lithium", pinned=True)])
    assert "actualizacion" not in client.get("/").text
    checks.latest_check = check([update("A", "Sodium")])
    assert "1 actualización" in client.get("/").text and "1 actualizaciones" not in client.get("/").text


def test_every_update_opens_its_card_from_the_pack_with_one_fill() -> None:
    client, _, checks, modrinth = setup_all()
    checks.latest_check = check([update("A", "Sodium"), update("C", "Lithium", pinned=True)])
    text = client.get(f"/modpacks/{BUNDLE_ID}/actualizaciones").text
    for project_id, title in (("A", "Sodium"), ("C", "Lithium")):
        query = f"?pack={BUNDLE_ID}"
        assert f'<a href="/mods/{project_id}{query}" hx-get="/mods/{project_id}/panel{query}"' in text
        assert title in text  # the check's title when the catalog has none
    assert modrinth.filled == [["A", "C"]]


def test_the_page_without_updates_does_not_ask_modrinth() -> None:
    client, _, checks, modrinth = setup_all()
    client.get(f"/modpacks/{BUNDLE_ID}/actualizaciones")
    checks.latest_check = check([])
    client.get(f"/modpacks/{BUNDLE_ID}/actualizaciones")
    assert modrinth.filled == []


def test_a_beta_and_a_stable_that_ends_one_carry_their_badge() -> None:
    # M2 (owner, 2026-10-12): the channel of what arrives is said when it matters.
    client, _, checks = setup()
    beta, leaves, plain = update("A", "Sodium"), update("B", "Iris"), update("C", "Lithium")
    beta.kind, leaves.kind = "beta", "leaves_beta"
    checks.latest_check = check([beta, leaves, plain])
    text = client.get(f"/modpacks/{BUNDLE_ID}/actualizaciones").text
    def row(project: str) -> str:
        return text[text.index(f'data-change="{project}"'):].split("</li>")[0]

    assert ">beta<" in row("A") and ">estable: deja la beta<" in row("B")
    assert "beta" not in row("C")


def test_searching_from_the_pack_queues_the_check_and_opens_its_page() -> None:
    """O9 D6 (owner, 2026-10-26): «Buscar actualizaciones de mods» starts the check as a job and
    opens its page, where it shows how far it is; one already running is opened the same."""
    client, _, checks = setup()
    response = client.post(f"/modpacks/{BUNDLE_ID}/actualizaciones/buscar", params={"abrir": "1"}, headers=HTMX)
    assert response.status_code == 200 and response.headers["HX-Redirect"] == f"/modpacks/{BUNDLE_ID}/actualizaciones"
    assert checks.calls == [("enqueue", str(BUNDLE_ID), EDITOR)]
    checks.busy = True
    again = client.post(f"/modpacks/{BUNDLE_ID}/actualizaciones/buscar", params={"abrir": "1"}, headers=HTMX)
    assert again.status_code == 200 and again.headers["HX-Redirect"] == f"/modpacks/{BUNDLE_ID}/actualizaciones"


def test_the_updates_page_shows_the_check_in_progress() -> None:
    client, modpack, checks = setup()
    import asyncio

    modpack.jobs = [asyncio.run(checks.enqueue(str(BUNDLE_ID), EDITOR))]
    text = client.get(f"/modpacks/{BUNDLE_ID}/actualizaciones").text
    part = text[text.index('id="check-job"'):text.index('id="updates-main"')]
    assert "Revisión de actualizaciones" in part and 'hx-trigger="every 2s"' in part
