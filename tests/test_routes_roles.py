"""Ninguna ruta que llame a un método de operador atiende a un editor, en /v1 ni en el panel.

Qué exige operador lo dice la especificación (`docs/superpowers/specs/2026-10-02-panel-web-design.md`
§2), y está escrito aquí, no derivado del registro de `operator_only`: un método que pierde el
decorador sale del registro, y un barrido que leyera el registro lo perdería también. El registro
se compara con la lista, así que quitar o agregar un `@operator_only` en `src/` hace fallar el test.

Las rutas se descubren, no se listan: se recorren las rutas reales de la app (también las del
panel y las que no están en el esquema) y, por AST, se eligen las funciones que llaman a un
atributo con el nombre de uno de esos métodos, en su cuerpo o en el de los helpers del mismo
módulo a los que llaman. A cada una se le manda un request con la sesión
de un editor, y la respuesta tiene que ser 403. El 403 sale de `operator_only` antes de tocar la
base o el panel: los tests no abren conexiones, así que una ruta que llegara a MongoDB daría un
error y no un 403.
"""
import ast
import inspect
import sys
import textwrap
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.params import Form
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from tests._actors import EDITOR, session_for
from tests._session import session_cookie

SECRET = "test-session-secret"
# La especificación, §2 "Qué exige operador".
EXPECTED_OPERATOR_METHODS = {
    "UpdateService.enqueue_update",
    "UpdateService.enqueue_rollback",
    "PterodactylService.server_link_bundle",
    "PterodactylService.import_server",
    "PterodactylService.import_confirm",
    "PterodactylService.import_local",
}
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def app() -> FastAPI:
    import main  # cablea el contenedor de dependencias
    from src.routers.v1 import include_routers_v1
    from src.routers.web import include_routers_web

    application = FastAPI()
    application.add_middleware(SessionMiddleware, secret_key=SECRET)
    include_routers_v1(application)
    include_routers_web(application)
    return application


def registered_in_src() -> set[str]:
    """`OPERATOR_METHODS` sin los métodos que definen los tests (como `Dummy.act`): solo los de
    una clase de un módulo `src.*`."""
    import main  # noqa: F401  importa todos los servicios: el decorador registra al definir la clase
    from src.library.auth.roles import OPERATOR_METHODS

    found = set()
    for qualname in OPERATOR_METHODS:
        class_name, method = qualname.rsplit(".", 1)
        for name, module in list(sys.modules.items()):
            cls = getattr(module, class_name, None) if name.startswith("src.") else None
            if inspect.isclass(cls) and cls.__module__.startswith("src.") and hasattr(cls, method):
                found.add(qualname)
    return found


def module_functions(module_name: str) -> dict[str, ast.AST]:
    """Las funciones de nivel de módulo, por nombre: los helpers a los que una ruta puede llamar."""
    tree = ast.parse(inspect.getsource(sys.modules[module_name]))
    return {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def calls_any(endpoint: Callable[..., Any], methods: set[str]) -> set[str]:
    """Los nombres de `methods` a los que la función llama como `<algo>.<método>(...)`, en su cuerpo
    o en el de una función del mismo módulo a la que llama por nombre, transitivamente."""
    function = inspect.unwrap(endpoint)
    helpers = module_functions(function.__module__)
    found: set[str] = set()
    visited: set[str] = set()
    pending: list[ast.AST] = [ast.parse(textwrap.dedent(inspect.getsource(function)))]
    while pending:
        for node in ast.walk(pending.pop()):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute) and node.func.attr in methods:
                found.add(node.func.attr)
            elif isinstance(node.func, ast.Name) and node.func.id in helpers and node.func.id not in visited:
                visited.add(node.func.id)
                pending.append(helpers[node.func.id])
    return found


def flat(dependant: Dependant) -> Iterator[Dependant]:
    yield dependant
    for sub in dependant.dependencies:
        yield from flat(sub)


def placeholder(name: str, annotation: Any) -> str:
    if name == "mode":
        return "A"
    return "1" if annotation is int else "x"


@dataclass
class OperatorRoute:
    method: str
    path: str
    key: tuple[str, str]  # (módulo, función) del endpoint
    calls: set[str]
    params: dict[str, str] = field(default_factory=dict)
    form: dict[str, str] = field(default_factory=dict)


def operator_routes(application: FastAPI, methods: set[str]) -> list[OperatorRoute]:
    routes = []
    for context in iter_route_contexts(application.routes):
        if not isinstance(context.original_route, APIRoute):
            continue
        endpoint, path, verbs = context.endpoint, context.path, context.methods
        assert endpoint is not None and path is not None and verbs is not None
        calls = calls_any(endpoint, methods)
        if not calls:
            continue
        dependants = list(flat(context.dependant))
        for param in (p for d in dependants for p in d.path_params):
            path = path.replace("{" + param.alias + "}", placeholder(param.alias, param.field_info.annotation))
        query = {p.alias: placeholder(p.alias, p.field_info.annotation)
                 for d in dependants for p in d.query_params if p.field_info.is_required()}
        form = {p.alias: placeholder(p.alias, p.field_info.annotation)
                for d in dependants for p in d.body_params
                if isinstance(p.field_info, Form) and (p.field_info.is_required() or p.alias == "confirm")}
        for method in sorted(verbs):
            routes.append(OperatorRoute(method, path, (endpoint.__module__, endpoint.__name__), calls, query, form))
    return routes


def test_the_operator_methods_are_the_ones_the_spec_lists() -> None:
    assert registered_in_src() == EXPECTED_OPERATOR_METHODS


def test_no_operator_route_lets_an_editor_through() -> None:
    application = app()
    names = {qualname.rsplit(".", 1)[-1] for qualname in EXPECTED_OPERATOR_METHODS}
    routes = operator_routes(application, names)
    # Cada método de operador tiene al menos una ruta que lo llama: si una ruta deja de
    # encontrarse (otro nombre, otra forma de llamarlo), el barrido no la pierde en silencio.
    assert set().union(*(r.calls for r in routes)) == names

    client = TestClient(application, follow_redirects=False)
    client.cookies.set("session", session_cookie(session_for(EDITOR), SECRET))
    answers = {}
    for route in routes:
        headers = {"Sec-Fetch-Site": "same-origin"} if route.method in UNSAFE else {}
        response = client.request(route.method, route.path, params=route.params,
                                  data=route.form or None, headers=headers)
        answers[(route.method, route.path, route.key)] = response.status_code
    assert {k: v for k, v in answers.items() if v != 403} == {}


def test_the_sweep_finds_a_route_by_what_it_calls() -> None:
    """El descubrimiento mismo: una ruta del panel, fuera del esquema y con un formulario, que
    llama a un método de operador, entra al barrido con sus parámetros."""
    from fastapi import APIRouter

    router = APIRouter()

    @router.post("/panel/{id}/thing", include_in_schema=False)
    async def thing(id: str, mode: str, confirm: Annotated[str, Form()]) -> None:
        service: Any = None
        await service.enqueue_rollback(id, mode, confirm)

    application = FastAPI()
    application.include_router(router)
    [route] = operator_routes(application, {"enqueue_rollback"})
    assert (route.method, route.path, route.params, route.form) == ("POST", "/panel/x/thing", {"mode": "A"}, {"confirm": "x"})
    assert route.key == (__name__, "thing")


async def _rollback_through_a_helper(actor: Any) -> None:
    await _and_another_helper(actor)


async def _and_another_helper(actor: Any) -> None:
    import main

    await main.container.updateService().enqueue_rollback("x", "x", "A", actor=actor)


def test_the_sweep_follows_helpers_of_the_same_module() -> None:
    """Una ruta que llama al método de operador a través de funciones del módulo (como las de
    `src/routers/web/home.py`) también entra al barrido, y responde 403 a un editor."""
    from fastapi import APIRouter, Depends

    from src.dependences import get_current_actor

    router = APIRouter()

    @router.post("/panel/{id}/indirect", include_in_schema=False)
    async def indirect(id: str, actor: Any = Depends(get_current_actor)) -> None:
        await _rollback_through_a_helper(actor)

    application = FastAPI()
    application.add_middleware(SessionMiddleware, secret_key=SECRET)
    application.include_router(router)
    [route] = operator_routes(application, {"enqueue_rollback"})
    assert (route.method, route.path, route.calls) == ("POST", "/panel/x/indirect", {"enqueue_rollback"})

    client = TestClient(application)
    client.cookies.set("session", session_cookie(session_for(EDITOR), SECRET))
    assert client.post(route.path, headers={"Sec-Fetch-Site": "same-origin"}).status_code == 403
