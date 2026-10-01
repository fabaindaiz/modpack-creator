from fastapi import APIRouter
from src.dependences import *

router = APIRouter(
    tags=["users"],
)

@router.get('/users/me')
@inject
async def users_me(
        users: UsersService = Depends(Provide[Dependences.usersService]),
        current_user: UsersInternal = Depends(get_current_user)
        ):
    response = await users.user_get(str(current_user.id))
    return response