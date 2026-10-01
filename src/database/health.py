import asyncio
from src.model.users import UsersInternal

PING_TIMEOUT = 2.0

class HealthDatabase():
    async def ping(self) -> bool:
        """
        Whether MongoDB answers a ping within PING_TIMEOUT seconds.

        Any registered Document reaches the same client; a database that is down, or Beanie not
        initialized, answers False instead of raising.

        :return: True if MongoDB answered.
        """
        try:
            client = UsersInternal.get_pymongo_collection().database.client
            await asyncio.wait_for(client.admin.command("ping"), PING_TIMEOUT)
            return True
        except Exception:
            return False
