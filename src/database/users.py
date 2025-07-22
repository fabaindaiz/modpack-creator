from beanie import PydanticObjectId
from beanie.operators import In
from beanie.odm.documents import FindMany
from typing import Iterable, Optional
from src.model.users import (
    UsersInternal,
    UsersProvider,
)
from src.exceptions import *

class UsersDatabase():
    async def user_get(self, user_id: str) -> UsersInternal:
        """
        Get a user by their ID.
        
        :param user_id: The ID of the user to retrieve.
        :return: The UsersInternal object.
        """
        document = await UsersInternal.get(user_id)
        if not document:
            raise ValueError(f"User with id '{user_id}' not found.")
        return document

    async def user_insert(self, user: UsersInternal) -> UsersInternal:
        """
        Create a new user in the database.
        
        :param user: The UsersInternal object to create.
        :return: The created UsersInternal object.
        """
        return await UsersInternal.create(user)
    
    async def user_from_provider(self, usersProvider: UsersProvider) -> UsersInternal:
        """
        Get a user by their email from the provider.
        
        :param usersProvider: The UsersProvider object containing user data.
        :return: The UsersInternal object.
        """
        unauth_user = usersProvider.users_export()
        return await UsersInternal.find_one(UsersInternal.email == unauth_user.email) or await UsersInternal.insert(unauth_user)