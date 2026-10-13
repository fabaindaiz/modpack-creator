"""The image's build context (final review of batch B, 2026-10-13): `COPY scripts/build-icons.mjs` met
a `.dockerignore` that excludes `scripts/`, and the image no longer built. Every path the Dockerfile
copies from the context must survive `.dockerignore` (its last matching rule wins, `!` re-includes)."""
import fnmatch
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def ignored(path: str, rules: list[str]) -> bool:
    result = False
    for rule in rules:
        negate = rule.startswith("!")
        pattern = rule[1:] if negate else rule
        pattern = pattern.removeprefix("**/").rstrip("/")
        if fnmatch.fnmatch(path, pattern) or path.startswith(pattern + "/") or fnmatch.fnmatch(Path(path).name, pattern):
            result = not negate
    return result


def test_every_copied_path_is_in_the_build_context() -> None:
    rules = [line.strip() for line in (ROOT / ".dockerignore").read_text().splitlines()
             if line.strip() and not line.startswith("#")]
    copied = []
    for line in (ROOT / "docker/Dockerfile").read_text().splitlines():
        found = re.match(r"\s*COPY\s+(?!--from)(.+)", line)
        if found:
            *sources, _ = found.group(1).split()
            copied += [s for s in sources if not s.startswith("--")]
    assert "scripts/build-icons.mjs" in copied
    assert [s for s in copied if ignored(s.removeprefix("./"), rules)] == []
