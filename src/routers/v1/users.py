from fastapi import APIRouter
from src.dependences import *

router = APIRouter(
    tags=["users"],
)

@router.get('/users/me')
@inject
async def users_me(
        usersDB: UsersDatabase = Depends(Provide[Dependences.usersDatabase]),
        current_user: UsersInternal = Depends(get_current_user)
        ):
    response = await usersDB.user_get(str(current_user.id))
    return response