"""
The guided set-up (batch B, B1; d-7985ee-20935c): from what is known of a server or a pack, the
steps to a tested build. Each is done, offers its action, or (when the step before it is not done)
says what it waits for. Each says in one line why it matters, from what pack authors repeat
(docs/research/2026-10-12-construccion-de-modpacks.md). Pure: the facts come from the services.
"""
import logging
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, NamedTuple, Optional, TypeVar

logger = logging.getLogger("panel.onboarding")
T = TypeVar("T")


async def fact(read: Awaitable[T], default: T) -> T:
    """One fact of the set-up, or the default when reading it fails (spec, Errores): the step stays
    undone and the page still shows, never a 500."""
    try:
        return await read
    except Exception:
        logger.warning("a set-up fact could not be read", exc_info=True)
        return default


WHY = {
    "scan": "El escaneo dice qué corre hoy: es el punto de partida, y ningún mod baja de lo que ya corre.",
    "pack": "El pack es la lista de lo que debería correr; se puede crear desde lo escaneado.",
    "mods": "Los mods del pack; sus dependencias las agrega la resolución.",
    "resolve": "El pack se resuelve solo con cada cambio: elige una versión para cada mod y sus dependencias; lo que queda por decidir es tuyo.",
    "build": "Un build congela lo que se instala: lo que se prueba es exactamente lo que se instala.",
    "server": "El build se prueba e instala en un servidor vinculado al pack.",
    "simulate": "Simular muestra qué cambia, las bajas y los jars sin explicar, antes de tocar el servidor.",
}


@dataclass(frozen=True)
class Step:
    key: str
    label: str
    done: bool
    detail: str
    why: str
    # (label, href) of what to do now; None when done or waiting for an earlier step.
    action: Optional[tuple[str, str]]
    # The label of the earlier step it waits for, when that one is not done.
    waits_for: Optional[str]
    # Said instead of an action the person cannot take (an editor, an operator's step).
    note: Optional[str] = None


class ServerFacts(NamedTuple):
    scanned: bool
    identified: int
    bundle_id: Optional[str]
    bundle_name: Optional[str]
    # The pack's saved resolution is current (the pack did not change since).
    resolved: bool
    # Mods left to decide plus problems, in that resolution.
    pending: int
    latest_build: Optional[int]
    # The build of the newest finished simulation (or real update) on this server.
    simulated_build: Optional[int]
    # Of `pending`, the problems: «Decidir» leads to them first (O9 user-walk, A4).
    problems: int = 0


class PackFacts(NamedTuple):
    projects: int
    resolved: bool
    pending: int
    latest_build: Optional[int]
    # (id, name) of the servers linked to the pack.
    linked_servers: tuple[tuple[str, str], ...]
    # A linked server simulated (or installed) the latest build.
    simulated: bool
    # Of `pending`, the problems: «Decidir» leads to them first (O9 user-walk, A4).
    problems: int = 0


# What each step needs done first; a step not listed (the scan, linking) never waits.
NEEDS: dict[str, dict[str, tuple[str, ...]]] = {
    "server": {"resolve": ("pack",), "build": ("resolve",), "simulate": ("build",)},
    "pack": {"resolve": ("mods",), "build": ("resolve",), "simulate": ("build", "server")},
}


# The steps only an operator takes (d-7985ee-edef1e): linking a pack to a server, and simulating.
OPERATOR_STEPS: dict[str, frozenset[str]] = {
    "server": frozenset({"pack", "simulate"}),
    "pack": frozenset({"server", "simulate"}),
}
OPERATOR_NOTE = "Lo hace un operador."


def _chain(steps: list[tuple[str, str, bool, str, tuple[str, str]]], needs: dict[str, tuple[str, ...]],
           operator_only: frozenset[str] = frozenset(), operator: bool = True) -> list[Step]:
    """Steps in order: one whose needed step is not done waits for the first such, instead of
    offering its action; an operator's step, to anyone else, says so instead of offering it (user
    walk, 2026-10-13: it led an editor to a disabled control)."""
    built: dict[str, Step] = {}
    for key, label, done, detail, action in steps:
        blocking = next((built[n] for n in needs.get(key, ()) if not built[n].done), None)
        waits = blocking is not None and not done
        not_theirs = not done and not operator and key in operator_only
        built[key] = Step(key=key, label=label, done=done, detail=detail, why=WHY[key],
                          action=None if done or waits or not_theirs else action,
                          waits_for=blocking.label if waits and blocking is not None else None,
                          note=OPERATOR_NOTE if not_theirs else None)
    return list(built.values())


def _resolve_step(bundle_id: Optional[str], resolved: bool, pending: int,
                  problems: int = 0) -> tuple[str, str, bool, str, tuple[str, str]]:
    done = resolved and pending == 0
    detail = f"{pending} por decidir o con problemas" if resolved and pending else ""
    # The tab with what is pending, problems first, as the dashboard (O9 user-walk, A4). No «Resolver»
    # (O9 D2): the pack resolves by itself, and opening it resolves one that is not yet.
    vista = "problemas" if problems else "decidir"
    action = ("Decidir", f"/modpacks/{bundle_id}?vista={vista}") if resolved \
        else ("Abrir el pack", f"/modpacks/{bundle_id}#mods-section")
    return "resolve", "Nada por decidir", done, detail, action


def server_steps(server_id: str, facts: ServerFacts, operator: bool = True) -> list[Step]:
    """A server's steps: scanned, a pack linked, the pack resolved with nothing pending, a build,
    and that build simulated on this server."""
    simulated = facts.latest_build is not None and facts.simulated_build == facts.latest_build
    return _chain([
        ("scan", "Escaneado", facts.scanned, f"{facts.identified} mods identificados" if facts.scanned else "",
         ("Escanear", f"/servers/{server_id}#escaneo")),
        ("pack", "Pack vinculado", facts.bundle_id is not None, facts.bundle_name or "",
         ("Crear o vincular", f"/servers/{server_id}#modpack")),
        _resolve_step(facts.bundle_id, facts.resolved, facts.pending, facts.problems),
        ("build", "Build creado", facts.latest_build is not None,
         f"Build {facts.latest_build}" if facts.latest_build is not None else "",
         ("Crear build", f"/modpacks/{facts.bundle_id}#builds")),
        ("simulate", "Simulado en este servidor", simulated, "",
         ("Simular", f"/servers/{server_id}#actualizar")),
    ], NEEDS["server"], OPERATOR_STEPS["server"], operator)


def pack_steps(bundle_id: str, facts: PackFacts, operator: bool = True) -> list[Step]:
    """A pack's steps: mods, resolved with nothing pending, a build, a linked server, and the build
    simulated on one of them."""
    first = facts.linked_servers[0][0] if facts.linked_servers else None
    return _chain([
        ("mods", "Con mods", facts.projects > 0, f"{facts.projects} mods" if facts.projects else "",
         # The search heads the section; #mods starts below it (O9 user-walk, A5).
         ("Buscar mods", f"/modpacks/{bundle_id}#mods-section")),
        _resolve_step(bundle_id, facts.resolved, facts.pending, facts.problems),
        ("build", "Build creado", facts.latest_build is not None,
         f"Build {facts.latest_build}" if facts.latest_build is not None else "",
         ("Crear build", f"/modpacks/{bundle_id}#builds")),
        ("server", "Servidor vinculado", bool(facts.linked_servers),
         ", ".join(name for _, name in facts.linked_servers), ("Vincular un servidor", "/servers")),
        ("simulate", "Simulado", facts.simulated, "",
         ("Simular", f"/servers/{first}#actualizar") if first else ("Vincular un servidor", "/servers")),
    ], NEEDS["pack"], OPERATOR_STEPS["pack"], operator)


def pending_count(saved: Optional[tuple[Any, bool]]) -> int:
    """Mods left to decide plus problems in a saved resolution (`(resolution, stale)`), 0 without one."""
    if saved is None:
        return 0
    lock = saved[0].lock
    return len(lock.undecided) + len(lock.problems)


def problem_count(saved: Optional[tuple[Any, bool]]) -> int:
    """The problems in a saved resolution (`(resolution, stale)`), 0 without one."""
    return len(saved[0].lock.problems) if saved is not None else 0
