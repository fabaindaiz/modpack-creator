"""Ninguna ruta que llame a un método de operador atiende a un editor, en /v1 ni en el panel.

Qué exige operador lo dice la tabla `## Acciones y roles` de `docs/decisions.md`, no el registro de
`operator_only`: un método que pierde el decorador sale del registro, y un barrido que leyera el
registro lo perdería también. El registro se compara con las filas de operador, así que quitar o
agregar un `@operator_only` en `src/` hace fallar el test. Además, toda ruta que escribe (POST, PUT,
PATCH, DELETE) tiene que llegar a un método de la tabla: un método nuevo que escribe, o una ruta
nueva, no pasa sin su fila.

Las rutas se descubren, no se listan: se recorren las rutas reales de la app (también las del
panel y las que no están en el esquema) y, por AST, se eligen las funciones que llaman a un
atributo con el nombre de uno de esos métodos, en su cuerpo o en el de los helpers del mismo
módulo a los que llaman. A cada una se le manda un request con la sesión
de un editor, y la respuesta tiene que ser 403. El 403 sale de `operator_only` antes de tocar la
base o el panel: los tests no abren conexiones, así que una ruta que llegara a MongoDB daría un
error y no un 403.

La tabla también se exige desde el lado de los servicios: todo método que audita (llama a
`<algo>.audit.record(`) tiene su fila, y toda fila audita o está en `NOT_AUDITED` con su razón. Así
un método que escribe y no llega a ninguna ruta todavía, o que una ruta alcanza junto a otro ya
clasificado, no se escapa.

Límites conocidos:
- El AST compara por nombre de atributo, no por tipo. Una ruta que llamara a un `.resolve()` ajeno
  (de otra clase) contaría como clasificada, y un método de servicio con el mismo nombre que otro
  de una clase distinta se confundiría con él (por eso la tabla no repite nombres).
- Las rutas GET (y HEAD, OPTIONS) no se clasifican: solo se barren POST, PUT, PATCH y DELETE. Una
  GET que escribiera pasaría sin fila; que ninguna escriba lo cuida la revisión, no este test.
- Que un método audita se detecta solo con la forma literal `<x>.audit.record(` en su propio
  cuerpo: un `audit.record(` sobre una variable local, un alias o un helper que audite por él no
  cuentan, y ese método tendría que ir en `NOT_AUDITED` o auditar directamente.
"""
import ast
import inspect
import re
import sys
import textwrap
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.params import Form
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from tests._actors import EDITOR, session_for
from tests._session import session_cookie

SECRET = "test-session-secret"
ROLES = ("operador", "editor")
DECISIONS = Path(__file__).resolve().parent.parent / "docs" / "decisions.md"
SECTION = "## Acciones y roles"
ROW = re.compile(r"^\|\s*`(?P<method>[A-Za-z_]\w*\.[A-Za-z_]\w*)`\s*\|\s*(?P<role>[^|]*?)\s*\|\s*(?P<what>[^|]+?)\s*\|\s*$")
# Rutas que escriben y no llaman a un método de servicio de la tabla. Solo autenticación, y solo si
# alguna es POST; cada una con su razón. Hoy ninguna lo es, y una nueva tiene que justificarse aquí.
UNCLASSIFIED_WRITE_ROUTES: dict[tuple[str, str], str] = {}
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
# Métodos de operador que solo corre un trabajo, con el método de operador que lo encola: ninguna
# ruta los llama, la ruta llama al que encola, y el barrido exige esa ruta en su lugar (T1).
JOB_ONLY: dict[str, str] = {"import_server": "import_request"}
SERVICES = Path(__file__).resolve().parent.parent / "src" / "services"
# Filas de la tabla cuyo método no llama a `audit.record`, cada una con su razón (verificada leyendo
# el método). Un método que escribe algo que importa audita; estos solo tocan el catálogo local o
# una copia derivada que se recalcula (la última resolución).
NOT_AUDITED: dict[str, str] = {
    "PterodactylService.panel_refresh": ("lee la lista de servidores del panel y la guarda (una copia que se renueva "
                                         "cada 5 min); no cambia nada en el panel ni en un pack"),
    "ModpackService.resolve": ("calcula el lock, guarda en el catálogo de Modrinth lo que trae y guarda la última "
                               "resolución (ModpackResolution, una copia derivada que solo se muestra); no cambia "
                               "ningún bundle"),
    "ModpackService.mod_list": "es una lectura que solo guarda en el catálogo los proyectos (íconos) que trae de Modrinth",
    "ModpackService.project_versions": "es una lectura que solo guarda en el catálogo las versiones que trae de Modrinth",
    "ModpackService.mod_page": "es una lectura que solo guarda en el catálogo los proyectos que trae de Modrinth",
    "ModpackService.mod_versions_all": "es una lectura que solo guarda en el catálogo las versiones que trae de Modrinth",
    "ModrinthService.projects_filled": "es una lectura que solo guarda en el catálogo los proyectos que trae de Modrinth",
    "ModrinthService.project_create": "solo escribe un proyecto en el catálogo local, copiado de Modrinth",
    "ModrinthService.project_create_many": "solo escribe proyectos en el catálogo local, copiados de Modrinth",
    "ModrinthService.project_update": "solo refresca un proyecto del catálogo local desde Modrinth",
    "ModrinthService.project_update_many": "solo refresca proyectos del catálogo local desde Modrinth",
}


def app() -> FastAPI:
    import main  # cablea el contenedor de dependencias
    from src.routers.v1 import include_routers_v1
    from src.routers.web import include_routers_web

    application = FastAPI()
    application.add_middleware(SessionMiddleware, secret_key=SECRET)
    include_routers_v1(application)
    include_routers_web(application)
    return application


def load_role_table(text: str | None = None) -> dict[str, Literal["operador", "editor"]]:
    """La tabla `## Acciones y roles` de `docs/decisions.md`: método -> rol. Falla nombrando el
    problema si falta la sección, si una fila está mal formada, tiene un rol desconocido o repite
    un método, o si no hay ninguna fila."""
    lines = (DECISIONS.read_text() if text is None else text).splitlines()
    if SECTION not in lines:
        raise AssertionError(f"docs/decisions.md no tiene la sección {SECTION!r}")
    body = []
    for line in lines[lines.index(SECTION) + 1:]:
        if line.startswith("## "):
            break
        body.append(line)
    table: dict[str, Literal["operador", "editor"]] = {}
    header = False
    for line in body:
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells == ["Método", "Rol", "Qué hace"]:
            header = True
            continue
        if not header or set(line.replace("|", "").strip()) <= {"-", ":", " "}:
            continue
        match = ROW.match(line)
        if not match:
            raise AssertionError(f"fila mal formada en la tabla de roles: {line!r}")
        method, role = match["method"], match["role"]
        if role not in ROLES:
            raise AssertionError(f"rol desconocido {role!r} en la fila de {method}: use uno de {ROLES}")
        if method in table:
            raise AssertionError(f"{method} aparece dos veces en la tabla de roles")
        table[method] = role  # type: ignore[assignment]
    if not header:
        raise AssertionError(f"la sección {SECTION!r} no tiene el encabezado '| Método | Rol | Qué hace |'")
    if not table:
        raise AssertionError("la tabla de roles está vacía")
    return table


def operator_methods(table: Mapping[str, str]) -> set[str]:
    return {m for m, role in table.items() if role == "operador"}


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
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return str(next(iter(annotation)).value)  # a valid member, so the request reaches the service
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


def test_the_operator_rows_of_the_table_are_the_registered_operator_methods() -> None:
    assert operator_methods(load_role_table()) == registered_in_src()


def test_every_row_of_the_table_names_a_service_method_once() -> None:
    import main  # noqa: F401
    table = load_role_table()
    names = [m.rsplit(".", 1)[1] for m in table]
    assert len(names) == len(set(names)), "dos filas con el mismo nombre de método: el barrido las confundiría"
    for qualname in table:
        class_name, method = qualname.split(".")
        classes = [getattr(module, class_name, None) for name, module in list(sys.modules.items()) if name.startswith("src.services.")]
        assert any(inspect.isclass(c) and callable(getattr(c, method, None)) for c in classes), f"{qualname} no existe en src/services"


def test_every_write_route_reaches_a_method_of_the_table() -> None:
    table = load_role_table()
    names = {m.rsplit(".", 1)[1] for m in table}
    unclassified = []
    for context in iter_route_contexts(app().routes):
        if not isinstance(context.original_route, APIRoute):
            continue
        endpoint, verbs = context.endpoint, context.methods
        assert endpoint is not None and verbs is not None
        for verb in sorted(verbs & UNSAFE):
            key = (endpoint.__module__, endpoint.__name__)
            if not calls_any(endpoint, names) and key not in UNCLASSIFIED_WRITE_ROUTES:
                unclassified.append(f"{verb} {context.path} ({key[0]}.{key[1]})")
    assert unclassified == [], "rutas que escriben y no llegan a ningún método de la tabla de roles de docs/decisions.md: " + ", ".join(unclassified)


def service_methods() -> dict[str, bool]:
    """Cada método de una clase de `src/services/*.py`, como `Clase.método`, y si su cuerpo llama a
    `<algo>.audit.record(...)`."""
    methods: dict[str, bool] = {}
    for path in sorted(SERVICES.glob("*.py")):
        for cls in (n for n in ast.parse(path.read_text()).body if isinstance(n, ast.ClassDef)):
            for fn in (n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
                methods[f"{cls.name}.{fn.name}"] = any(
                    isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "record"
                    and isinstance(c.func.value, ast.Attribute) and c.func.value.attr == "audit"
                    for c in ast.walk(fn))
    return methods


def test_every_audited_service_method_is_in_the_table() -> None:
    table = load_role_table()
    missing = sorted(m for m, audits in service_methods().items() if audits and m not in table)
    assert missing == [], "métodos de servicio que auditan y no tienen fila en la tabla de roles de docs/decisions.md: " + ", ".join(missing)


def test_every_table_row_audits_or_is_allowlisted() -> None:
    table = load_role_table()
    methods = service_methods()
    offenders = sorted(m for m in table if not methods.get(m, False) and m not in NOT_AUDITED)
    assert offenders == [], "filas de la tabla cuyo método no llama a audit.record y no están en NOT_AUDITED: " + ", ".join(offenders)
    stale = sorted(m for m in NOT_AUDITED if m not in table or methods.get(m, False))
    assert stale == [], "entradas de NOT_AUDITED que ya no están en la tabla o que ahora auditan: " + ", ".join(stale)


GOOD = f"{SECTION}\n\n| Método | Rol | Qué hace |\n|---|---|---|\n| `A.b` | editor | x |\n"


def test_a_badly_written_table_fails_naming_the_problem() -> None:
    import pytest

    assert load_role_table(GOOD) == {"A.b": "editor"}
    cases = {
        "no tiene la sección": "# nada\n",
        "rol desconocido 'operadorr'": GOOD + "| `A.c` | operadorr | y |\n",
        "fila mal formada": GOOD + "| A.c | editor | y |\n",
        "dos veces": GOOD + "| `A.b` | operador | y |\n",
        "está vacía": f"{SECTION}\n\n| Método | Rol | Qué hace |\n|---|---|---|\n",
        "no tiene el encabezado": f"{SECTION}\n\ntexto\n",
    }
    for message, text in cases.items():
        with pytest.raises(AssertionError, match=message):
            load_role_table(text)


def test_no_operator_route_lets_an_editor_through() -> None:
    application = app()
    names = {qualname.rsplit(".", 1)[-1] for qualname in operator_methods(load_role_table())}
    assert set(JOB_ONLY) <= names and set(JOB_ONLY.values()) <= names, "JOB_ONLY nombra métodos que no son de operador"
    names -= set(JOB_ONLY)
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
