"""Quién puede entrar (i-7985ee-c64e06): autenticarse en el IdP no basta para estar autorizado.

La decisión es una función pura sobre los claims del ID token, para probarla sin IdP ni base.
"""
import asyncio
from typing import Any, cast

import pytest
from starlette.requests import Request

from src.exceptions import HTTP_401_UNAUTHORIZED
from src.library.auth import authorize, parse_allowlist
from src.model.users import UsersProviderAuthentik

GROUPS = frozenset({"modpack-admins"})
EMAILS = frozenset({"owner@example.org"})


def claims(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "iss": "https://idp.example.org/application/o/modpack/",
        "sub": "abc123",
        "name": "Owner",
        "email": "someone@example.org",
        "email_verified": True,
        "groups": [],
    }
    base.update(overrides)
    return base


def test_member_of_an_allowed_group_is_authorized() -> None:
    assert authorize(claims(groups=["players", "modpack-admins"]), GROUPS, frozenset())


def test_allowlisted_email_is_authorized() -> None:
    assert authorize(claims(email="owner@example.org"), frozenset(), EMAILS)


def test_unverified_email_never_matches_the_email_allowlist() -> None:
    assert not authorize(claims(email="owner@example.org", email_verified=False), frozenset(), EMAILS)
    assert not authorize(claims(email="owner@example.org", email_verified=None), frozenset(), EMAILS)
    no_claim = claims(email="owner@example.org")
    del no_claim["email_verified"]
    assert not authorize(no_claim, frozenset(), EMAILS)


def test_group_membership_does_not_depend_on_the_email() -> None:
    # Authentik sends email_verified=False by default since 2025.10; a group is set by the IdP admin.
    assert authorize(claims(groups=["modpack-admins"], email_verified=False), GROUPS, frozenset())


def test_authenticated_but_not_allowed_is_refused() -> None:
    assert not authorize(claims(groups=["players"]), GROUPS, EMAILS)


def test_missing_groups_claim_only_leaves_the_email_path() -> None:
    no_groups = claims(email="owner@example.org")
    del no_groups["groups"]
    assert authorize(no_groups, GROUPS, EMAILS)
    assert not authorize(no_groups, GROUPS, frozenset())


def test_empty_configuration_refuses_everyone() -> None:
    assert not authorize(claims(groups=["modpack-admins"], email="owner@example.org"), frozenset(), frozenset())


def test_parse_allowlist() -> None:
    assert parse_allowlist(" a, b ,,c ") == frozenset({"a", "b", "c"})
    assert parse_allowlist("") == frozenset()
    assert parse_allowlist(None) == frozenset()


def test_identity_is_issuer_and_subject_not_email() -> None:
    stored = UsersProviderAuthentik(**claims())
    assert stored.users_validate(UsersProviderAuthentik(**claims(email="new-address@example.org")))
    assert not stored.users_validate(UsersProviderAuthentik(**claims(sub="someone-else")))
    assert not stored.users_validate(UsersProviderAuthentik(**claims(iss="https://other-idp.example.org/")))


def test_session_without_a_user_is_unauthenticated() -> None:
    from src.dependences import get_current_user

    request = Request({"type": "http", "session": {"_state_oauth2_x": "pending login"}})
    with pytest.raises(HTTP_401_UNAUTHORIZED):
        asyncio.run(get_current_user(request, users=cast(Any, None)))
