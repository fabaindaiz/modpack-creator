"""Actors, sessions and an audit double shared by the role and panel tests."""
import json
from typing import Any, Awaitable, Callable, Optional

from src.model.users.actor import Actor

OPERATOR = Actor(iss="https://idp.test", sub="op-1", name="Olga Operator",
                 groups=frozenset({"modpack-admins", "modpack-operators"}), is_operator=True)
EDITOR = Actor(iss="https://idp.test", sub="ed-1", name="Eddie Editor",
               groups=frozenset({"modpack-admins"}), is_operator=False)


def session_for(actor: Optional[Actor], *, legacy: bool = False) -> dict[str, Any]:
    """The session a login would store; `legacy=True` is one written before `groups` existed."""
    if actor is None:
        return {}
    user = {
        "email": f"{actor.sub}@idp.test",
        "name": actor.name,
        "provider": {"type": "authentik", "iss": actor.iss, "sub": actor.sub, "name": actor.name,
                     "email": f"{actor.sub}@idp.test", "email_verified": True},
    }
    session: dict[str, Any] = {"user": json.dumps(user)}
    if not legacy:
        session["groups"] = sorted(actor.groups)
    return session


class RecordingAudit:
    def __init__(self, fail: bool = False, on_record: Optional[Callable[[], Awaitable[Any]]] = None) -> None:
        self.fail = fail
        self.on_record = on_record
        self.events: list[tuple[str, Any, Any, Any, Any]] = []

    async def record(self, actor: Actor, action: str, target: Any, details: Any = None) -> None:
        hook_result = await self.on_record() if self.on_record else None
        if self.fail:
            raise RuntimeError("audit failed")
        self.events.append((action, target, details, hook_result, actor.ref()))
