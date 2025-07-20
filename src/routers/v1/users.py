from fastapi import APIRouter
from src.dependences import *

router = APIRouter(
    tags=["users"],
)

@router.get('/users/me')
@inject
async def users_me(
        users_service: UsersService = Depends(Provide[Dependences.usersService]),
        current_user: UsersInternal = Depends(get_current_user)
        ):
    response = await users_service.users_get_id(current_user.id) # type: ignore
    return response