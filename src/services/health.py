from src.database.health import HealthDatabase

class HealthService():
    def __init__(self,
            healthDB: HealthDatabase,
            ) -> None:
        self.healthDB = healthDB

    async def ready(self) -> bool:
        """
        Whether the API can serve requests: today, whether MongoDB answers.
        """
        return await self.healthDB.ping()
