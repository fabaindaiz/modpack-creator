from collections.abc import Mapping
from typing import Any, Optional


def parse_allowlist(value: Optional[str]) -> frozenset[str]:
    """Parse a comma-separated allowlist from configuration. Empty or missing means nobody."""
    if not value:
        return frozenset()
    return frozenset(item.strip() for item in value.split(",") if item.strip())


def authorize(claims: Mapping[str, Any], allowed_groups: frozenset[str], allowed_emails: frozenset[str]) -> bool:
    """Decide whether an authenticated identity may use the API.

    Fails closed: the identity must belong to an allowed group, or have an allowlisted email that
    the IdP marks as verified. Group membership is set by the IdP's admin, so it does not depend on
    the email (authentik sends `email_verified: false` by default since 2025.10). With both
    allowlists empty, nobody is authorized.
    """
    groups = claims.get("groups") or []
    if isinstance(groups, list) and allowed_groups.intersection(str(g) for g in groups):
        return True
    return claims.get("email_verified") is True and claims.get("email") in allowed_emails
