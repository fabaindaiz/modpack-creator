#!/bin/bash
# Gate: verifica la copia de .agents/ (checksums, enlaces, privacidad, carrier.toml).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=""
for c in python3.14 python3.13 python3.12 python3.11 /opt/homebrew/bin/python3.14 python3; do
  if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    PY="$c"; break
  fi
done
[ -n "$PY" ] || { echo "check.sh: se necesita Python 3.11+" >&2; exit 1; }
"$PY" .agents/tools/bundle.py verify
