import backoff
from typing import Optional
from src.model.userdata.users import UsersInternal
from src.library.tinydb import TinyDBDatabase, where
from src.library.utils import getenv, auto_id

USERDATA_USERS_FILE = getenv("USERS_INTERNAL_FILE", "data/userdata/users.json")

class UsersDatabase(TinyDBDatabase[UsersInternal]):
    def __init__(self):
        super().__init__(model_cls=UsersInternal, file=USERDATA_USERS_FILE)

    def __enter__(self) -> 'UsersDatabase':
        super().__enter__()
        return self
    
    def __exit__(self, exc_type, exc_value, traceback) -> None:
        super().__exit__(exc_type, exc_value, traceback)

    async def users_exists(self, id: str) -> bool:
        """
        Check if a user with the given id exists in the database.
        """
        return self.exists(where('id') == id)

    async def users_exists_email(self, email: str) -> bool:
        """
        Check if a user with the given email exists in the database.
        """
        return self.exists(where('email') == email)
    
    @backoff.on_exception(backoff.expo, ValueError, max_tries=5)
    async def users_create_auto_id(self, data: dict, overwrite: bool = False) -> UsersInternal:
        """
        Create a new user with an auto-generated id.
        """
        return await self.users_create(id=auto_id(), data=data, overwrite=overwrite)

    async def users_create(self, id: str, data: dict, overwrite: bool = False) -> UsersInternal:
        """
        Create a new user in the database.
        """
        if overwrite:
            self.delete(where('id') == id)
        if await self.users_exists(id):
            raise ValueError(f"User with id '{id}' already exists.")
        data.update({"id": id})
        return self.insert_one(data)

    async def users_get_id(self, id: str) -> UsersInternal:
        """
        Get a user by its id.
        """
        if not await self.users_exists(id):
            raise ValueError(f"User with id '{id}' does not exist.")
        return self.get_one(where('id') == id)

    async def users_get_email(self, email: str) -> UsersInternal:
        """
        Get a user by their email.
        """
        if not await self.users_exists_email(email):
            raise ValueError(f"User with email '{email}' does not exist.")
        return self.get_one(where('email') == email)

    async def users_update(self, id: str, data: dict) -> UsersInternal:
        """
        Update an existing user in the database.
        """
        if not await self.users_exists(id):
            raise ValueError(f"User with id '{id}' does not exist.")
        return self.update_one(data, where('id') == id)

    async def users_delete(self, id: str) -> int:
        return self.delete(where('id') == id)