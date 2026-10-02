from src.database.health import HealthDatabase
from src.services.jobs import WorkerBeat

class HealthService():
    def __init__(self,
            healthDB: HealthDatabase,
            workerBeat: WorkerBeat,
            ) -> None:
        self.healthDB = healthDB
        self.workerBeat = workerBeat

    async def ready(self) -> bool:
        """
        Whether the API can serve requests: MongoDB answers and, if this process runs a job
        worker, the worker is beating.
        """
        return await self.healthDB.ping() and self.workerBeat.healthy()
