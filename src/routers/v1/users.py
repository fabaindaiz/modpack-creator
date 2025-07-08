from fastapi import APIRouter
from src.dependences import *

router = APIRouter(
    tags=["users"],
)

@router.get('/users/me')
async def users_me(
        current_user: str = Depends(get_current_user)
        ):
    return current_user