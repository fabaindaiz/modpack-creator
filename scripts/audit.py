"""Comprueba el repositorio contra las reglas que él mismo escribe sobre sí.

Cada regla de abajo está escrita en AGENTS.md (§Invariantes) o en docs/decisions.md. Si una
regla cambia allí, cambia aquí también; si un chequeo de aquí no tiene regla, no debería
fallar el gate.

Dos severidades: los FALLOS detienen el gate; los AVISOS se imprimen siempre y no detienen
nada. Cada chequeo es una función sobre texto, para que tests/test_audit.py la vea fallar.
"""
from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Routers que hoy inyectan una base de datos o un cliente en vez de un servicio. La lista solo
# se achica: una violación nueva falla, y una entrada que ya no viola también falla, para que
# el arreglo la saque de aquí en el mismo cambio (docs/roadmap.md, capas).
LAYERING_BASELINE = {
    ("src/routers/v1/modpack.py", "bundle_find", "modpackDatabase"),
    ("src/routers/v1/modpack.py", "bundle_create", "modpackDatabase"),
    ("src/routers/v1/modpack.py", "bundle_get", "modpackDatabase"),
    ("src/routers/v1/modpack.py", "bundle_update", "modpackDatabase"),
    ("src/routers/v1/modpack.py", "project_get", "modpackDatabase"),
    ("src/routers/v1/modpack.py", "modpack_delete_project", "modpackDatabase"),
    ("src/routers/v1/modrinth.py", "project_find", "modrinthDatabase"),
    ("src/routers/v1/modrinth.py", "project_get", "modrinthDatabase"),
    ("src/routers/v1/pterodactyl.py", "server_external", "pterodactylAPI"),
    ("src/routers/v1/pterodactyl.py", "server_find", "pterodactylDatabase"),
    ("src/routers/v1/pterodactyl.py", "server_get_id", "pterodactylDatabase"),
    ("src/routers/v1/pterodactyl.py", "version_find", "pterodactylDatabase"),
    ("src/routers/v1/pterodactyl.py", "version_get", "pterodactylDatabase"),
    ("src/routers/v1/users.py", "users_me", "usersDatabase"),
}
# Lo que un router puede inyectar además de un servicio: el cliente OAuth del login.
ROUTER_ALLOWED_PROVIDERS = {"authlib_oauth"}

# Documents que existen sin registrarse en init_beanie. Solo se achica.
UNREGISTERED_BASELINE = {"PterodactylModpack"}

MUTABLE_DEFAULT = (ast.Dict, ast.List, ast.Set, ast.DictComp, ast.ListComp, ast.SetComp)
NON_IDEMPOTENT = {"POST", "PATCH", "PUT", "DELETE"}


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def mutable_defaults(source: str, name: str) -> list[str]:
    """Ningún argumento por defecto mutable: se comparte entre llamadas (d-7985ee-147399)."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            defaults = [*node.args.defaults, *(d for d in node.args.kw_defaults if d is not None)]
            for default in defaults:
                if isinstance(default, MUTABLE_DEFAULT) or (
                        isinstance(default, ast.Call) and isinstance(default.func, ast.Name)
                        and default.func.id in {"dict", "list", "set"}):
                    label = getattr(node, "name", "lambda")
                    found.append(f"{name}:{default.lineno} {label}: argumento por defecto mutable")
    return found


def router_injections(source: str, name: str) -> set[tuple[str, str, str]]:
    """(archivo, función, provider) por cada Provide[Dependences.x] que no es un servicio."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Subscript) and isinstance(sub.value, ast.Name) and sub.value.id == "Provide"
                    and isinstance(sub.slice, ast.Attribute)):
                provider = sub.slice.attr
                if not provider.endswith("Service") and provider not in ROUTER_ALLOWED_PROVIDERS:
                    found.add((name, node.name, provider))
    return found


def layering(found: set[tuple[str, str, str]], baseline: set[tuple[str, str, str]]) -> list[str]:
    """Los routers pasan por un servicio (d-7985ee-16ed28). La baseline solo se achica."""
    problems = [f"{f}: {fn} inyecta {p}; un router solo inyecta servicios" for f, fn, p in sorted(found - baseline)]
    problems += [f"{f}: {fn} ya no inyecta {p}; sácalo de LAYERING_BASELINE en scripts/audit.py"
                 for f, fn, p in sorted(baseline - found)]
    return problems


def documents_defined(source: str) -> set[str]:
    return {node.name for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ClassDef)
            and any(isinstance(b, ast.Name) and b.id == "Document" for b in node.bases)}


def documents_registered(source: str) -> set[str]:
    registered: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "init_beanie":
            for kw in node.keywords:
                if kw.arg == "document_models" and isinstance(kw.value, ast.List):
                    registered |= {e.id for e in kw.value.elts if isinstance(e, ast.Name)}
    return registered


def registration(defined: set[str], registered: set[str], baseline: set[str]) -> list[str]:
    """Todo Document está en init_beanie (d-7985ee-8eb944): si no, usarlo lanza CollectionWasNotInitialized."""
    problems = [f"{d}: Document sin registrar en src/model/__init__.py (init_beanie)"
                for d in sorted(defined - registered - baseline)]
    problems += [f"{d}: ya está registrado; sácalo de UNREGISTERED_BASELINE en scripts/audit.py"
                 for d in sorted(baseline & registered)]
    return problems


def required_env(source: str) -> set[str]:
    """Variables leídas con getenv sin default: sin ellas la app no importa."""
    names = set()
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "getenv"
                and len(node.args) == 1 and not node.keywords and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            names.add(node.args[0].value)
    return names


def env_template(required: set[str], template: str) -> list[str]:
    """Toda variable obligatoria aparece en .env.default (d-7985ee-2e4d10)."""
    keys = set(re.findall(r"^([A-Z][A-Z0-9_]*):", template, re.MULTILINE))
    return [f"{name}: obligatoria (getenv sin default) y ausente de .env.default" for name in sorted(required - keys)]


def python_versions(dockerfile: str, mypy_ini: str, pyproject: str) -> list[str]:
    """Una sola versión de Python: la imagen, mypy y hatch (d-7985ee-7bc399)."""
    found = {
        "docker/Dockerfile": re.search(r"python:(\d+\.\d+)", dockerfile),
        ".mypy.ini": re.search(r"^python_version\s*=\s*(\d+\.\d+)", mypy_ini, re.MULTILINE),
    }
    versions = {k: m.group(1) if m else None for k, m in found.items()}
    versions["pyproject.toml"] = tomllib.loads(pyproject)["tool"]["hatch"]["envs"]["default"].get("python")
    if None in versions.values() or len(set(versions.values())) != 1:
        return [f"versiones de Python distintas o ilegibles: {versions}"]
    return []


def pinned_requirements(text: str) -> list[str]:
    """Cada dependencia lleva versión exacta y aparece una vez (d-7985ee-cd8f73): sin eso, CI y la
    imagen instalan lo más nuevo cada vez y una release externa rompe el gate sin cambiar el repo."""
    problems, seen = [], set()
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        name = re.split(r"[=<>!~\[; ]", line, maxsplit=1)[0].lower().replace("_", "-")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9,._-]+\])?==[A-Za-z0-9.+!_-]+", line):
            problems.append(f"requirements.txt:{number} {line}: sin versión exacta (==)")
        if name in seen:
            problems.append(f"requirements.txt:{number} {name}: repetida")
        seen.add(name)
    return problems


def retried_writes(source: str, name: str) -> list[str]:
    """AVISO (d-7985ee-05d032): un reintento sobre un POST/PATCH/PUT/DELETE puede repetir un efecto en el servidor."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        retries = any("backoff" in ast.unparse(d) for d in node.decorator_list)
        methods = {m.group(1) for m in re.finditer(r"METHOD\.([A-Z]+)", ast.unparse(node))}
        if retries and methods & NON_IDEMPOTENT:
            found.append(f"{name}:{node.lineno} {node.name}: reintenta un {'/'.join(sorted(methods))}")
    return found


def main() -> int:
    sources = {rel(p): p.read_text() for p in [ROOT / "main.py", *sorted((ROOT / "src").rglob("*.py")),
                                               *sorted((ROOT / "scripts").glob("*.py")),
                                               *sorted((ROOT / "tests").glob("*.py"))]}
    failures: list[str] = []
    for name, text in sources.items():
        failures += mutable_defaults(text, name)
    injections: set[tuple[str, str, str]] = set()
    for name, text in sources.items():
        if name.startswith("src/routers/"):
            injections |= router_injections(text, name)
    failures += layering(injections, LAYERING_BASELINE)
    defined = set().union(*(documents_defined(t) for n, t in sources.items() if n.startswith("src/model/")))
    failures += registration(defined, documents_registered(sources["src/model/__init__.py"]), UNREGISTERED_BASELINE)
    required = set().union(*(required_env(t) for t in sources.values()))
    failures += env_template(required, (ROOT / ".env.default").read_text())
    failures += python_versions((ROOT / "docker/Dockerfile").read_text(), (ROOT / ".mypy.ini").read_text(),
                                (ROOT / "pyproject.toml").read_text())
    failures += pinned_requirements((ROOT / "requirements.txt").read_text())
    advisories = [w for n, t in sources.items() if n.startswith("src/library/api/client/") for w in retried_writes(t, n)]

    for line in advisories:
        print(f"AVISO {line}")
    for line in failures:
        print(f"FALLO {line}")
    print(f"audit: {len(failures)} fallos, {len(advisories)} avisos; baseline de capas {len(LAYERING_BASELINE)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
