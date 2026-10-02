import asyncio
from types import SimpleNamespace

import pytest

from src.exceptions import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN
from src.library.auth.roles import OPERATOR_METHODS, actor_from_session, operator_only
from tests._actors import EDITOR, OPERATOR, session_for

OPERATOR_GROUPS = frozenset({"modpack-operators"})


def test_member_of_operator_group_is_operator() -> None:
    actor = actor_from_session(session_for(OPERATOR), OPERATOR_GROUPS)
    assert actor.is_operator
    assert (actor.iss, actor.sub, actor.name) == (OPERATOR.iss, OPERATOR.sub, OPERATOR.name)


def test_allowed_but_not_operator_is_editor() -> None:
    assert not actor_from_session(session_for(EDITOR), OPERATOR_GROUPS).is_operator


def test_legacy_session_without_groups_is_editor() -> None:
    actor = actor_from_session(session_for(OPERATOR, legacy=True), OPERATOR_GROUPS)
    assert not actor.is_operator
    assert actor.groups == frozenset()


def test_empty_operator_groups_makes_nobody_operator() -> None:
    assert not actor_from_session(session_for(OPERATOR), frozenset()).is_operator


def test_session_without_user_is_401() -> None:
    with pytest.raises(HTTP_401_UNAUTHORIZED):
        actor_from_session(session_for(None), OPERATOR_GROUPS)


RAN: list[int] = []


class Dummy:
    @operator_only
    async def act(self, actor, value):
        RAN.append(value)
        return value


def test_operator_only_refuses_editor_before_running() -> None:
    RAN.clear()
    with pytest.raises(HTTP_403_FORBIDDEN):
        asyncio.run(Dummy().act(EDITOR, 1))
    with pytest.raises(HTTP_403_FORBIDDEN):
        asyncio.run(Dummy().act(actor=EDITOR, value=1))
    assert RAN == []


def test_operator_only_runs_for_operator() -> None:
    assert asyncio.run(Dummy().act(OPERATOR, 7)) == 7
    assert asyncio.run(Dummy().act(actor=OPERATOR, value=8)) == 8


def test_operator_only_registers_the_method() -> None:
    assert "Dummy.act" in OPERATOR_METHODS


def test_operator_only_needs_an_actor_parameter() -> None:
    with pytest.raises(TypeError):
        @operator_only
        async def act(self, value):
            return None


@pytest.mark.network
def test_new_session_still_reads_as_user(mongo) -> None:
    from src.dependences import get_current_user

    async def scenario() -> None:
        request = SimpleNamespace(session=session_for(OPERATOR))
        user = await get_current_user(request=request, users=None)  # type: ignore[arg-type]
        assert user.provider.sub == OPERATOR.sub

    mongo(scenario)
