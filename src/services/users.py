from collections.abc import Mapping
from typing import Any
from src.database.users import UsersDatabase
from src.library.auth import authorize, parse_allowlist
from src.library.utils import getenv, getenv_opt
from src.model.users import UsersInternal, UsersProviderAuthentik
from src.exceptions import *

# Who may use the API besides being able to log in to the IdP (i-7985ee-c64e06). Required, so a
# deploy that forgets it does not start; an empty value authorizes nobody.
OIDC_ALLOWED_GROUPS = parse_allowlist(getenv("OIDC_ALLOWED_GROUPS"))
OIDC_ALLOWED_EMAILS = parse_allowlist(getenv_opt("OIDC_ALLOWED_EMAILS"))

class UsersService():
    def __init__(self,
            usersDB: UsersDatabase) -> None:
        self.usersDB = usersDB

    async def login(self, claims: Mapping[str, Any]) -> UsersInternal:
        """
        Authorize the claims of a verified ID token and return the matching user.

        :param claims: The ID token claims (`token["userinfo"]`).
        :return: The UsersInternal object, created on the first authorized login.
        """
        if not authorize(claims, OIDC_ALLOWED_GROUPS, OIDC_ALLOWED_EMAILS):
            raise HTTP_403_FORBIDDEN(f"User '{claims.get('email')}' is not allowed to use this API")
        return await self.usersDB.user_from_provider(UsersProviderAuthentik(**claims))
