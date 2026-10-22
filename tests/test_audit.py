"""Cada chequeo de scripts/audit.py se ve fallar sobre un caso malo y pasar sobre uno bueno.

Un chequeo que nunca falla es un verde constante, no un chequeo.
"""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("audit", Path(__file__).parent.parent / "scripts" / "audit.py")
assert _spec and _spec.loader
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)


def test_mutable_defaults() -> None:
    assert audit.mutable_defaults("def f(headers: dict = {}): pass", "x.py")
    assert audit.mutable_defaults("async def f(*, ids=list()): pass", "x.py")
    assert not audit.mutable_defaults("def f(headers=None, loaders=()): pass", "x.py")


def test_layering() -> None:
    router = (
        "async def handler(db = Depends(Provide[Dependences.modpackDatabase]),\n"
        "                  svc = Depends(Provide[Dependences.modpackService]),\n"
        "                  oauth = Depends(Provide[Dependences.authlib_oauth])): pass\n")
    found = audit.router_injections(router, "r.py")
    assert found == {("r.py", "handler", "modpackDatabase")}
    assert audit.layering(found, set())
    assert audit.layering(set(), found)
    assert not audit.layering(found, found)


def test_registration() -> None:
    models = "class A(Document): pass\nclass B(Document): pass\nclass C(BaseModel): pass\n"
    init = "async def f():\n    await init_beanie(database=x, document_models=[A])\n"
    defined, registered = audit.documents_defined(models), audit.documents_registered(init)
    assert defined == {"A", "B"} and registered == {"A"}
    assert audit.registration(defined, registered, set())
    assert not audit.registration(defined, registered, {"B"})
    assert audit.registration(defined, registered | {"B"}, {"B"})


def test_env_template() -> None:
    required = audit.required_env('X = getenv("NEEDED")\nY = getenv("OPTIONAL", "d")\nZ = getenv_opt("MAYBE")')
    assert required == {"NEEDED"}
    assert audit.env_template(required, 'OTHER: "x"\n')
    assert not audit.env_template(required, 'NEEDED: "x"\n')


def test_python_versions() -> None:
    pyproject = '[tool.hatch.envs.default]\npython = "3.12"\n'
    assert not audit.python_versions("ARG python=python:3.12-slim", "python_version = 3.12", pyproject)
    assert audit.python_versions("ARG python=python:3.13-slim", "python_version = 3.12", pyproject)


def test_pinned_requirements() -> None:
    assert not audit.pinned_requirements("fastapi==0.142.2\nuvicorn[standard]==0.37.0  # server\n\n")
    assert audit.pinned_requirements("fastapi\n")
    assert audit.pinned_requirements("fastapi>=0.100\n")
    assert audit.pinned_requirements("fastapi==1.0\nFastAPI==1.0\n")


def test_retried_writes() -> None:
    client = ("@backoff.on_exception(backoff.expo, Exception, max_tries=2)\n"
              "async def power(self):\n    await self._request(method=METHOD.POST, path='p')\n"
              "@backoff.on_exception(backoff.expo, Exception, max_tries=2)\n"
              "async def read(self):\n    await self._request(method=METHOD.GET, path='p')\n")
    assert [w.split()[1] for w in audit.retried_writes(client, "c.py", set())] == ["power:"]
    # Un POST que solo lee se declara, y entonces se puede reintentar.
    assert not audit.retried_writes(client, "c.py", {("c.py", "power")})


def test_an_idempotent_post_that_no_longer_retries_leaves_the_list() -> None:
    client = "async def power(self):\n    await self._request(method=METHOD.POST, path='p')\n"
    assert audit.stale_idempotent_posts({("c.py", "power"), ("c.py", "gone")}, {"c.py": client}) == [
        "c.py gone: ya no reintenta un POST; sácalo de IDEMPOTENT_POSTS en scripts/audit.py",
        "c.py power: ya no reintenta un POST; sácalo de IDEMPOTENT_POSTS en scripts/audit.py"]


def test_an_unpublished_commit_by_another_identity_fails() -> None:
    """Bundle 0.0.32: the attribution has a second channel, the commit's author and committer. In a
    fresh environment git may be configured as the assistant, and no trailer says so."""
    owner = "Dueño <dueno@example.org>"
    log = [f"abc1234\t{owner}\t{owner}", f"def5678\tAsistente <bot@example.org>\t{owner}",
           f"0123abc\t{owner}\tAsistente <bot@example.org>"]
    assert audit.commit_identities(log, {owner}) == [
        "commit def5678: autor Asistente <bot@example.org>, que no es el dueño del repositorio",
        "commit 0123abc: committer Asistente <bot@example.org>, que no es el dueño del repositorio"]
    assert not audit.commit_identities(log[:1], {owner})
    assert not audit.commit_identities([], {owner})


def _git(repo: Path, *args: str) -> str:
    import subprocess
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=Dueño", "-c", "user.email=dueno@example.org",
                           "-c", "commit.gpgsign=false", *args], capture_output=True, text=True, check=True).stdout


def _commit(repo: Path, subject: str) -> None:
    _git(repo, "commit", "--allow-empty", "-q", "-m", subject)


def test_the_identity_check_reads_the_branch_against_its_upstream(tmp_path: Path) -> None:
    """In CI the clone has no commit off a remote (the checkout is the remote's branch): the workflow
    makes the branch's base its upstream, and the check reads `@{u}..HEAD`, as `bundle.py trailers`
    does. Without an upstream, the commits on no remote, as before."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "on main")
    _git(repo, "branch", "ci-base")
    _git(repo, "switch", "-q", "-c", "work")
    _commit(repo, "on the branch")
    # No upstream and no remote: every commit is unpublished.
    assert [line.split("\t")[0] for line in audit.unpublished_log(repo)] == [
        _git(repo, "rev-parse", "--short", "HEAD").strip(), _git(repo, "rev-parse", "--short", "HEAD~1").strip()]
    _git(repo, "branch", "--set-upstream-to=ci-base")
    assert [line.split("\t")[0] for line in audit.unpublished_log(repo)] == [
        _git(repo, "rev-parse", "--short", "HEAD").strip()]
