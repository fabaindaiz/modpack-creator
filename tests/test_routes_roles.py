"""Ninguna ruta que llame a un método de operador atiende a un editor.

Las rutas se descubren, no se listan: por AST, las funciones de `src/routers/**` cuyo cuerpo llama
a un atributo con el nombre de un método de `OPERATOR_METHODS` (los decorados con
`operator_only`). A cada una se le manda un request con la sesión de un editor, y la respuesta
tiene que ser 403. El 403 sale de `operator_only` antes de tocar la base o el panel: los tests no
abren conexiones, así que una ruta que llegara a MongoDB daría un error y no un 403.
"""
import ast
import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from tests._actors import EDITOR, session_for
from tests._session import session_cookie

ROUTERS = Path(__file__).resolve().parent.parent / "src" / "routers"
SECRET = "test-session-secret"
# En /v1: asociar a un bundle, importar, confirmar, dejar local, actualizar y rollback.
OPERATOR_ROUTES_TODAY = 6


def app() -> FastAPI:
    import main  # cablea el contenedor de dependencias
    from src.routers.v1 import include_routers_v1

    application = FastAPI()
    application.add_middleware(SessionMiddleware, secret_key=SECRET)
    include_routers_v1(application)
    return application


def operator_method_names() -> set[str]:
    # Importar los servicios llena OPERATOR_METHODS (el decorador registra al definir la clase).
    import src.services  # noqa: F401
    from src.library.auth.roles import OPERATOR_METHODS

    return {qualname.rsplit(".", 1)[-1] for qualname in OPERATOR_METHODS}


def operator_handlers(methods: set[str]) -> set[str]:
    """Nombres de las funciones de router cuyo cuerpo llama a `<algo>.<método de operador>(...)`."""
    found = set()
    for path in ROUTERS.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if any(isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr in methods
                   for call in ast.walk(node)):
                found.add(node.name)
    return found


def operation_id(name: str, path: str, method: str) -> str:
    """El id único que FastAPI le da a una ruta por defecto (fastapi.utils.generate_unique_id)."""
    return f"{re.sub(r'\W', '_', name + path)}_{method}"


def test_no_operator_route_lets_an_editor_through() -> None:
    application = app()
    handlers = operator_handlers(operator_method_names())
    # openapi() y no app.routes: desde FastAPI 0.142 los routers incluidos son perezosos.
    routes = []
    matched = set()
    for path, item in application.openapi()["paths"].items():
        for method, operation in item.items():
            name = next((h for h in handlers if operation.get("operationId") == operation_id(h, path, method)), None)
            if name is None:
                continue
            matched.add(name)
            query = {p["name"]: ("A" if p["name"] == "mode" else "x")
                     for p in operation.get("parameters", []) if p["in"] == "query" and p.get("required")}
            routes.append((method.upper(), re.sub(r"\{[^}]+\}", "x", path), query))
    # Una función que no esté en el esquema escaparía del barrido: tiene que aparecer en él.
    assert matched == handlers
    # Las rutas se encuentran a través del decorador: un método que pierde `@operator_only` sale
    # también del barrido. Este piso, la cantidad de hoy, es lo que lo hace visible. Solo crece.
    assert len(routes) >= OPERATOR_ROUTES_TODAY

    client = TestClient(application)
    client.cookies.set("session", session_cookie(session_for(EDITOR), SECRET))
    answers = {(method, path): client.request(method, path, params=query).status_code
               for method, path, query in routes}
    assert {k: v for k, v in answers.items() if v != 403} == {}
