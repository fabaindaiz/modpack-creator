from src.database.users import UsersDatabase
from src.model.users import UsersInternal, UsersProvider
from src.exceptions import *

class UsersService():
    def __init__(self,
            usersDB: UsersDatabase) -> None:
        self.usersDB = usersDB

    async def provider_validate(self, usersProvider: UsersProvider) -> UsersInternal:
        """
        Validate a user by their email.

        :param email: The email of the user to validate.
        :return: The UsersInternal object.
        """
        database_user = await self.usersDB.user_from_provider(usersProvider)
        if database_user.provider.users_validate(usersProvider):
            return database_user
        raise HTTP_401_UNAUTHORIZED(f"User '{usersProvider.email}' cannot be validated")