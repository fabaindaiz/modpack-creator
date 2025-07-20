from src.model.userdata.users import UsersInternal, UsersProvider

class UsersService():
    def __init__(self) -> None:
        pass

    async def users_get_id(self, id: str) -> UsersInternal:
        """
        Get a user by their id.

        :param id: The id of the user to retrieve.
        :return: The UsersInternal object.
        """
        document = await UsersInternal.get(id)
        if not document:
            raise ValueError(f"User with id '{id}' not found.")
        return document

    async def users_provider_handler(self, usersProvider: UsersProvider) -> UsersInternal:
        """
        Get a user by their email.
        
        :param email: The email of the user to retrieve.
        :return: The UsersInternal object.
        """
        unauth_user = usersProvider.users_export()
        return await UsersInternal.find_one(UsersInternal.email == unauth_user.email) or await UsersInternal.insert(unauth_user)

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