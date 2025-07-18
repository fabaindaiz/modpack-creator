from src.database.userdata.users import UsersDatabase
from src.model.userdata.users import UsersInternal, UsersProvider

class UsersService():
    def __init__(self,
            usersDB: UsersDatabase
            ) -> None:
        self.usersDB = usersDB

    async def users_get_id(self, id: str) -> UsersInternal:
        """
        Get a user by their id.

        :param id: The id of the user to retrieve.
        :return: The UsersInternal object.
        """
        with self.usersDB as usersDB:
            return await usersDB.users_get_id(id)

    async def users_provider_handler(self, usersProvider: UsersProvider) -> UsersInternal:
        """
        Get a user by their email.
        
        :param email: The email of the user to retrieve.
        :return: The UsersInternal object.
        """
        unauth_user = usersProvider.users_export()
        with self.usersDB as usersDB:
            try:
                return await usersDB.users_get_email(unauth_user.email)
            except ValueError:
                return await usersDB.users_create_auto_id(data=unauth_user.model_dump())

    async def users_provider_validate(self, usersProvider: UsersProvider) -> UsersInternal:
        """
        Validate a user by their email.

        :param email: The email of the user to validate.
        :return: The UsersInternal object.
        """
        database_user = await self.users_provider_handler(usersProvider)
        if database_user.provider.users_validate(usersProvider):
            return database_user
        raise ValueError(f"User with email '{usersProvider.email}' does not match the provider data.")