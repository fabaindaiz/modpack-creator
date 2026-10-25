#!/bin/bash
# Integra los commits de un delegado uno por uno, cada uno sobre el gate: cherry-pick sin commit, el
# gate, y el commit con el mensaje y el autor originales. Se detiene en el primero que falla, con el
# cambio en el árbol para verlo. Uso: scripts/pick-through-gate.sh <commit>...
#   GATE  el comando del gate (por defecto scripts/check.sh)
set -euo pipefail
# En el repositorio donde se corre (el árbol principal), no en el del script.
cd "$(git rev-parse --show-toplevel)"
[ "$#" -gt 0 ] || { echo "uso: scripts/pick-through-gate.sh <commit>..." >&2; exit 2; }
gate="${GATE:-scripts/check.sh}"
for commit in "$@"; do
  sha="$(git rev-parse --verify "$commit^{commit}")"
  echo "== $(git log -1 --format='%h %s' "$sha")"
  git cherry-pick --no-commit "$sha"
  if ! $gate; then
    echo "pick-through-gate.sh: el gate falló en $sha; el cambio quedó en el árbol" >&2
    exit 1
  fi
  git commit --quiet --reuse-message="$sha"
done
