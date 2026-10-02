from dataclasses import dataclass

from pydantic import BaseModel


class ActorRef(BaseModel):
    """Who did something, as stored: the identity is `iss` + `sub`; `name` is only for display."""
    iss: str
    sub: str
    name: str


@dataclass(frozen=True)
class Actor:
    """The caller of a request, derived from the session on every request (never stored)."""
    iss: str
    sub: str
    name: str
    groups: frozenset[str]
    is_operator: bool

    def ref(self) -> ActorRef:
        return ActorRef(iss=self.iss, sub=self.sub, name=self.name)
