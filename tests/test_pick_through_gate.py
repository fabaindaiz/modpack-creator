"""Integrar los commits de un delegado, cada uno sobre el gate (fricción contada dos veces, 2026-10-21/22):
`scripts/pick-through-gate.sh` hace cherry-pick sin commit, corre el gate y comitea con el mensaje y el
autor originales; en el primer gate rojo se detiene y deja el cambio en el árbol. En un repositorio de
git descartable, con un gate de mentira."""
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "pick-through-gate.sh"
ENV = {**os.environ, "GIT_AUTHOR_NAME": "Delegado", "GIT_AUTHOR_EMAIL": "d@example.invalid",
       "GIT_COMMITTER_NAME": "Dueño", "GIT_COMMITTER_EMAIL": "o@example.invalid", "GIT_CONFIG_GLOBAL": os.devnull}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, env=ENV, check=True, capture_output=True, text=True).stdout.strip()


def repo_with_a_delegate_branch(tmp_path: Path) -> tuple[Path, list[str]]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("a\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "chore: base")
    git(repo, "checkout", "-q", "-b", "delegado")
    shas = []
    for name in ("b", "c"):
        (repo / f"{name}.txt").write_text(f"{name}\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", f"feat: {name}")
        shas.append(git(repo, "rev-parse", "HEAD"))
    git(repo, "checkout", "-q", "main")
    return repo, shas


def pick(repo: Path, gate: str, *shas: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(SCRIPT), *shas], cwd=repo, env={**ENV, "GATE": gate},
                          capture_output=True, text=True)


def test_each_commit_lands_on_a_green_gate_with_its_message_and_author(tmp_path: Path) -> None:
    repo, shas = repo_with_a_delegate_branch(tmp_path)
    assert pick(repo, "true", *shas).returncode == 0
    assert git(repo, "log", "--format=%s|%an", "-3").splitlines() == ["feat: c|Delegado", "feat: b|Delegado",
                                                                      "chore: base|Delegado"]


def test_a_red_gate_stops_with_the_change_left_in_the_tree(tmp_path: Path) -> None:
    repo, shas = repo_with_a_delegate_branch(tmp_path)
    result = pick(repo, "false", *shas)
    assert result.returncode == 1
    assert git(repo, "log", "--format=%s", "-1") == "chore: base"
    assert git(repo, "status", "--porcelain") == "A  b.txt"
