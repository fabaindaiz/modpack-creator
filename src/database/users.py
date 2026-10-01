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
        Get the user an already authorized provider identity belongs to, by issuer and subject.

        Users stored before identities carried a subject are matched once by issuer and email,
        and the subject is written to them. Otherwise the user is created.

        :param usersProvider: The UsersProvider object containing user data.
        :return: The UsersInternal object.
        """
        user = await UsersInternal.find_one({"provider.iss": usersProvider.iss, "provider.sub": usersProvider.sub})
        if user:
            return user
        legacy = await UsersInternal.find_one({
            "provider.iss": usersProvider.iss,
            "provider.sub": {"$exists": False},
            "email": usersProvider.email,
        })
        if legacy:
            return await legacy.set({"provider": usersProvider.model_dump(), "name": usersProvider.name})
        return await UsersInternal.insert(usersProvider.users_export())