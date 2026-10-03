#!/bin/bash
# La revisión visual del panel (R10): se corre a mano, no va en CI ni en `hatch run gate`.
# Uso: ./scripts/visual-sweep.sh [salida]   (por defecto un directorio temporal que imprime)
# Compila el CSS, levanta un MongoDB descartable, siembra datos de demo, corre la app con
# configuración falsa DESDE UN DIRECTORIO FUERA DEL REPOSITORIO (así `.env.yaml` nunca se carga),
# captura las páginas y apaga todo al salir. Sale distinto de 0 si alguna página falla.
# Primera vez: `hatch run visual:playwright install chromium` (o PLAYWRIGHT_CHROMIUM=<ruta>).
set -euo pipefail
cd "$(dirname "$0")/.."
repo="$PWD"
command -v hatch >/dev/null || { echo "visual-sweep.sh: falta hatch (https://hatch.pypa.io)" >&2; exit 1; }
out="${1:-$(mktemp -d -t visual-sweep.XXXXXX)}"
mkdir -p "$out"
out="$(cd "$out" && pwd)"
work="$(mktemp -d -t visual-sweep-work.XXXXXX)"
name="modpack-sweep-mongo-$$"
cleanup() {
  docker stop "$name" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

npm run build >&2
py="$(hatch run visual:python -c 'import sys; print(sys.executable)' | tail -n 1)"
port="$("$py" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
docker run -d --rm --name "$name" -p "127.0.0.1:$port:27017" docker.io/library/mongo:8.0 >/dev/null
until docker exec "$name" mongosh --quiet --eval 'db.adminCommand("ping")' >/dev/null 2>&1; do sleep 1; done

echo "capturas en: $out" >&2
cd "$work"
status=0
# La siembra importa src.model, que exige MONGODB_URI; la app recibe el resto de la configuración falsa.
MONGODB_URI="mongodb://127.0.0.1:$port" PYTHONPATH="$repo" "$py" "$repo/scripts/visual_sweep.py" "mongodb://127.0.0.1:$port" "$out" || status=$?
echo "capturas en: $out" >&2
exit "$status"
