import functools
import inspect
import json
from collections.abc import Mapping
from typing import Any, Callable

from src.exceptions import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN
from src.model.users.actor import Actor

# "Class.method" of every method decorated with `operator_only`.
OPERATOR_METHODS: set[str] = set()


def operator_only(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Restrict an async method with an `actor: Actor` parameter to operators.

    The check runs before the body awaits anything. A method without an `actor` parameter is a
    programming error and fails when decorated, not when called.
    """
    signature = inspect.signature(fn)
    if "actor" not in signature.parameters:
        raise TypeError(f"{fn.__qualname__} is operator_only but has no 'actor' parameter")
    OPERATOR_METHODS.add(fn.__qualname__)

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        actor = signature.bind(*args, **kwargs).arguments.get("actor")
        if not isinstance(actor, Actor) or not actor.is_operator:
            raise HTTP_403_FORBIDDEN("This action requires the operator role")
        return await fn(*args, **kwargs)

    return wrapper


def actor_from_session(session: Mapping[str, Any], operator_groups: frozenset[str]) -> Actor:
    """Derive the Actor of a request from its session. Fails closed: no user is 401, and a
    session without valid groups (written before groups were stored) is an editor."""
    raw_user = session.get("user")
    if not raw_user:
        raise HTTP_401_UNAUTHORIZED("Not authenticated")
    try:
        user = json.loads(raw_user)
        iss, sub, name = user["provider"]["iss"], user["provider"]["sub"], user["name"]
    except (TypeError, ValueError, KeyError):
        raise HTTP_401_UNAUTHORIZED("Not authenticated")
    if not all(isinstance(v, str) and v for v in (iss, sub, name)):
        raise HTTP_401_UNAUTHORIZED("Not authenticated")
    raw_groups = session.get("groups")
    groups = frozenset(g for g in raw_groups if isinstance(g, str)) if isinstance(raw_groups, list) else frozenset()
    return Actor(
        iss=iss,
        sub=sub,
        name=name,
        groups=groups,
        is_operator=bool(operator_groups & groups),
    )
