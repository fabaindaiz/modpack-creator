#!/bin/bash
# El gate local: lo mismo que corre CI (.github/workflows/check.yml, d-7985ee-fd2a8d).
set -euo pipefail
cd "$(dirname "$0")/.."
command -v hatch >/dev/null || { echo "check.sh: falta hatch (https://hatch.pypa.io)" >&2; exit 1; }
exec hatch run gate
