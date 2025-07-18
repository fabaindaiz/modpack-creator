from fastapi import APIRouter, Request, Query
from fastapi.responses import RedirectResponse
from dependency_injector.wiring import Provide, inject
from typing import Optional
from src.model.userdata.users import UsersProviderAuthentik
from src.dependences import *

router = APIRouter(
    tags=["auth"],
)

@router.get('/login')
@inject
async def login(
        request: Request,
        redirect: str = Query("/", description="Redirect URL after login", regex=r"^/[a-zA-Z0-9/_\-\?\=\&]*$"),
        authlib_oauth: OAuth = Depends(Provide[Dependences.authlib_oauth])
    ):
    redirect_uri = request.url_for('login_callback').include_query_params(redirect=redirect)
    return await authlib_oauth.oauth2.authorize_redirect(request, redirect_uri)

@router.get('/login/callback')
@inject
async def login_callback(
        request: Request,
        redirect: str = Query("/", description="Redirect URL after login", regex=r"^/[a-zA-Z0-9/_\-\?\=\&]*$"),
        authlib_oauth: OAuth = Depends(Provide[Dependences.authlib_oauth]),
        users_service: UsersService = Depends(Provide[Dependences.usersService])
    ):
    try:
        token = await authlib_oauth.oauth2.authorize_access_token(request)
    except OAuthError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=error.description,
        )
    user = token.get('userinfo')
    if user:
        user_provider = UsersProviderAuthentik(**user)
        database_user = await users_service.users_provider_validate(user_provider)
        request.session['user'] = database_user.model_dump()
    return RedirectResponse(url=redirect)

@router.get('/logout')
async def logout(
        request: Request,
        redirect: str = Query("/", description="Redirect URL after login", regex=r"^/[a-zA-Z0-9/_\-\?\=\&]*$")
        ):
    request.session.pop('user', None)
    return RedirectResponse(url=redirect)