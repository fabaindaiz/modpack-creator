"""mypy rechaza una llamada a un método de operador sin `actor`.

`operator_only` conserva la firma del método (ParamSpec): quien lo llama sin `actor` no pasa
`hatch run typecheck`. Si el decorador se tipara como `Callable[..., Any]`, la firma se perdería
y esa llamada pasaría sin queja; este test lo detecta corriendo mypy sobre un snippet.
"""
import os
from pathlib import Path

from mypy import api

ROOT = Path(__file__).resolve().parent.parent

SNIPPET = """
from src.model.users.actor import Actor
from src.services.update import UpdateService


async def go(s: UpdateService, actor: Actor) -> None:
    await s.enqueue_update("x", {args})
"""


def check(tmp_path: Path, args: str) -> list[str]:
    """Las líneas de error de mypy sobre el snippet."""
    snippet = tmp_path / "snippet.py"
    snippet.write_text(SNIPPET.format(args=args))
    # `.mypy.ini` y los imports `src.*` se resuelven desde la raíz del repositorio.
    previous = Path.cwd()
    os.chdir(ROOT)
    try:
        stdout, _, _ = api.run([str(snippet), "--config-file", ".mypy.ini", "--no-incremental", "--no-color-output"])
    finally:
        os.chdir(previous)
    return [line for line in stdout.splitlines() if line.startswith(str(snippet)) and ": error:" in line]


def test_mypy_rejects_an_operator_call_without_its_actor(tmp_path: Path) -> None:
    errors = check(tmp_path, "build=1")
    assert any("actor" in line for line in errors), errors


def test_mypy_accepts_an_operator_call_with_its_actor(tmp_path: Path) -> None:
    assert check(tmp_path, "actor=actor, build=1") == []
