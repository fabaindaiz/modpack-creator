from fastapi import APIRouter, Depends
from dependency_injector.wiring import Provide, inject
from src.dependences import *

router = APIRouter(
    tags=["token"],
)

@router.get("/token")
@inject
async def token(
        ):
    return {}