from fastapi import APIRouter
from src.model.jobs import Job
from src.dependences import *

router = APIRouter(
    tags=["jobs"],
)

@router.get("/job/{id}", response_model=Job)
@inject
async def job_get(
        id: str,
        jobs: JobService = Depends(Provide[Dependences.jobService]),
        current_user = Depends(get_current_user)
        ):
    """A job: its status, attempts, the steps it finished and its error, if any."""
    return await jobs.get(id)
