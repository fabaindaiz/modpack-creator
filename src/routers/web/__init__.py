from fastapi import FastAPI
import src.routers.web.home as home
import src.routers.web.modpacks as modpacks
from src.web import LoginRequired, login_required_handler

WEB_MODULES = [
    home,
    modpacks,
]

def include_routers_web(app: FastAPI) -> None:
    """Mount the panel's pages, outside the API schema, and the handler that sends a request
    without a session to the login. Usable on a bare app: the route sweeps call it directly."""
    app.add_exception_handler(LoginRequired, login_required_handler)
    for module in WEB_MODULES:
        app.include_router(module.router, include_in_schema=False)
