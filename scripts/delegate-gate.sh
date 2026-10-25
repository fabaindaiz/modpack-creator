#!/bin/bash
# El gate sin hatch, para el worktree de un delegado: el sandbox no le deja crear su entorno de hatch,
# así que corre lo mismo que `hatch run gate` (pyproject.toml) con el Python del entorno del árbol
# principal. Uso: scripts/delegate-gate.sh [args de pytest]
#   GATE_PYTHON  el python de ese entorno (por defecto, el que dice `hatch env find` desde el árbol
#                principal; con espacios en la ruta se respeta entero).
set -euo pipefail
cd "$(dirname "$0")/.."
python="${GATE_PYTHON:-}"
if [ -z "$python" ]; then
  main="$(git worktree list --porcelain | sed -n '1s/^worktree //p')"
  command -v hatch >/dev/null || { echo "delegate-gate.sh: falta hatch, o pon GATE_PYTHON" >&2; exit 1; }
  python="$(cd "$main" && hatch env find default)/bin/python"
fi
[ -x "$python" ] || { echo "delegate-gate.sh: no hay python en «$python»" >&2; exit 1; }
"$python" .agents/tools/bundle.py verify
"$python" .agents/tools/bundle.py ids docs/decisions.md docs/roadmap.md .claude/logs/agent-changelog.md
"$python" .agents/tools/bundle.py decisions docs/decisions.md
"$python" .agents/tools/bundle.py trailers
"$python" scripts/audit.py
"$python" -m mypy main.py src tests scripts
exec "$python" -m pytest "$@"
