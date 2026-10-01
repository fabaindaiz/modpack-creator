from fastapi import APIRouter
from fastapi.responses import JSONResponse
from src.dependences import *

# Without a session: the compose healthcheck and the proxy ask, not a user (i-7985ee-90f944).
router = APIRouter(
    prefix="/health",
    tags=["health"],
)

@router.get('/live')
async def health_live() -> dict[str, str]:
    return {"status": "ok"}

@router.get('/ready')
@inject
async def health_ready(
        health: HealthService = Depends(Provide[Dependences.healthService]),
        ) -> JSONResponse:
    if await health.ready():
        return JSONResponse({"status": "ok"})
    return JSONResponse({"status": "unavailable"}, status_code=503)
