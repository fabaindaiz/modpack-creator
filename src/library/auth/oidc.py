from fastapi.security import OAuth2AuthorizationCodeBearer
from fastapi import Depends, HTTPException, status
from typing import Annotated

oauth2_scheme = OpenIdConnect(
    openIdConnectUrl="https://login.fadiaz.cl/application/o/modpack/",
    description="")

async def get_current_user(token: Annotated[str, Depends(oauth2_scheme)]):
    user = token
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    print(user)
    return user
