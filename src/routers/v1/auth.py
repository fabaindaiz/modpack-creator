from fastapi import APIRouter, Request, Query
from fastapi.responses import RedirectResponse
from dependency_injector.wiring import Provide, inject
from src.dependences import *
from src.exceptions import *

# A local path whose second character is never a slash: `//host` is protocol-relative and would
# send the browser to another host. The regex engine has no lookahead, hence the explicit shape.
LOCAL_PATH = r"^/([a-zA-Z0-9_\-\?\=\&][a-zA-Z0-9/_\-\?\=\&]*)?$"

router = APIRouter(
    tags=["auth"],
)

@router.get('/login')
@inject
async def login(
        request: Request,
        redirect: str = Query("/", description="Redirect URL after login", pattern=LOCAL_PATH),
        authlib_oauth: OAuth = Depends(Provide[Dependences.authlib_oauth])
    ):
    redirect_uri = request.url_for('login_callback').include_query_params(redirect=redirect)
    return await authlib_oauth.oauth2.authorize_redirect(request, redirect_uri)

@router.get('/login/callback')
@inject
async def login_callback(
        request: Request,
        redirect: str = Query("/", description="Redirect URL after login", pattern=LOCAL_PATH),
        authlib_oauth: OAuth = Depends(Provide[Dependences.authlib_oauth]),
        users: UsersService = Depends(Provide[Dependences.usersService])
    ):
    try:
        token = await authlib_oauth.oauth2.authorize_access_token(request)
    except OAuthError as error:
        raise HTTP_401_UNAUTHORIZED(error.description)
    claims = token.get('userinfo')
    if not claims:
        raise HTTP_401_UNAUTHORIZED("The identity provider returned no ID token claims")
    database_user = await users.login(claims)
    request.session['user'] = database_user.model_dump_json()
    request.session['groups'] = [str(g) for g in claims.get('groups') or [] if isinstance(g, str)]
    return RedirectResponse(url=redirect)

@router.get('/logout')
async def logout(
        request: Request,
        redirect: str = Query("/", description="Redirect URL after login", pattern=LOCAL_PATH)
        ):
    request.session.pop('user', None)
    request.session.pop('groups', None)
    return RedirectResponse(url=redirect)