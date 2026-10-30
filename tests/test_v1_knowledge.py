"""The knowledge base by /v1 (O8 tanda 3, D13 of d-7985ee-a99db7): the service's methods, with its roles. A status
sent when marking is ignored: a mark is always a suspicion (user-walk K22)."""
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.dependences import get_current_actor
from src.routers.v1 import include_routers_v1
from tests._actors import EDITOR, OPERATOR
from tests._modrinth import IRIS, SODIUM
from tests.conftest import MongoRunner
from tests.test_knowledge import IRIS_BROKEN, service

MARK = {"bundle_id": "b1", "a": {"project_id": IRIS, "broken_from": IRIS_BROKEN}, "b": {"project_id": SODIUM},
        "summary": "pantalla negra", "note_ids": ["n1"], "key": "k1"}


def client(actor: Any) -> AsyncClient:
    """On the scenario's own event loop: MongoDB's client is bound to it."""
    import main
    from dependency_injector import providers
    main.container.knowledgeService.override(providers.Object(service()[0]))
    app = FastAPI()
    include_routers_v1(app)
    app.dependency_overrides[get_current_actor] = lambda: actor
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver")


@pytest.mark.network
def test_v1_marks_and_reads_back(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[Any, ...]:
        v1 = client(EDITOR)
        made = await v1.post("/v1/knowledge", json=MARK)
        issue_id = made.json()["_id"] if "_id" in made.json() else made.json()["id"]
        listed = (await v1.get("/v1/knowledge", params={"project": IRIS})).json()
        joined = (await v1.post(f"/v1/knowledge/{issue_id}/join", json={"bundle_id": "b2", "note_ids": []})).json()
        return made.status_code, len(listed), (await v1.get(f"/v1/knowledge/{issue_id}")).json()["summary"], joined["seen_in"]

    status, listed, summary, seen = mongo(scenario)
    assert status == 201 and listed == 1 and summary == "pantalla negra" and seen == ["b1", "b2"]


@pytest.mark.network
def test_v1_status_in_the_body_is_ignored_on_create(mongo: MongoRunner) -> None:
    async def scenario() -> str:
        return str((await client(EDITOR).post("/v1/knowledge", json={**MARK, "status": "confirmed"})).json()["status"])

    assert mongo(scenario) == "suspected"


@pytest.mark.network
def test_v1_editor_status_change_is_403(mongo: MongoRunner) -> None:
    async def scenario() -> tuple[int, int]:
        made = (await client(EDITOR).post("/v1/knowledge", json=MARK)).json()
        issue_id = made.get("_id") or made["id"]
        body = {"rev": made["rev"], "status": "confirmed"}
        return ((await client(EDITOR).post(f"/v1/knowledge/{issue_id}/status", params=body)).status_code,
                (await client(OPERATOR).post(f"/v1/knowledge/{issue_id}/status", params=body)).status_code)

    assert mongo(scenario) == (403, 200)


@pytest.mark.network
def test_v1_join_widens_to_another_minecraft_only_when_asked(mongo: MongoRunner) -> None:
    """K10 of d-7985ee-c0cbfb: `also_game_version` widens the entry, with the rights of an edit."""
    async def scenario() -> tuple[Any, ...]:
        v1 = client(EDITOR)
        made = (await v1.post("/v1/knowledge", json={**MARK, "game_version": "1.21.1"})).json()
        issue_id = made.get("_id") or made["id"]
        plain = (await v1.post(f"/v1/knowledge/{issue_id}/join", json={"bundle_id": "b2"})).json()
        wide = (await v1.post(f"/v1/knowledge/{issue_id}/join",
                              json={"bundle_id": "b2", "also_game_version": "1.21.4"})).json()
        return plain["also_game_versions"], wide["also_game_versions"]

    assert mongo(scenario) == ([], ["1.21.4"])

