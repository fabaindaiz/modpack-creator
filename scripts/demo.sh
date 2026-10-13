#!/bin/bash
# La demo local del panel: los mismos datos de demo, configuración falsa y doble de Modrinth que el
# barrido visual (scripts/visual-sweep.sh), sobre un MongoDB descartable, con la app corriendo en
# http://127.0.0.1:${DEMO_PORT:-8765} y una ventana de Chromium con la sesión de operador ya abierta.
# Nada escribe en un servidor de juego. Se corre en una terminal propia (usa docker o podman) y se
# termina cerrando la ventana o con Ctrl+C; al salir apaga el MongoDB y la app.
# Primera vez: `npm ci` y `hatch run visual:playwright install chromium` (o PLAYWRIGHT_CHROMIUM=<ruta>).
set -euo pipefail
cd "$(dirname "$0")/.."
repo="$PWD"
command -v hatch >/dev/null || { echo "demo.sh: falta hatch (https://hatch.pypa.io)" >&2; exit 1; }
work="$(mktemp -d -t modpack-demo.XXXXXX)"
name="modpack-demo-mongo-$$"
cleanup() {
  docker stop "$name" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

npm run build >&2
py="$(hatch run visual:python -c 'import sys; print(sys.executable)' | tail -n 1)"
mongo_port="$("$py" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
docker run -d --rm --name "$name" -p "127.0.0.1:$mongo_port:27017" docker.io/library/mongo:8.0 --wiredTigerCacheSizeGB 0.25 >/dev/null
ready=0
for _ in $(seq 1 60); do
  if docker exec "$name" mongosh --quiet --eval 'db.adminCommand("ping")' >/dev/null 2>&1; then ready=1; break; fi
  if [ "$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null || echo false)" != "true" ]; then
    echo "demo.sh: el contenedor de MongoDB terminó antes de responder" >&2; exit 1
  fi
  sleep 1
done
[ "$ready" = 1 ] || { echo "demo.sh: MongoDB no respondió en 60 s" >&2; exit 1; }

# Desde un directorio fuera del repositorio: la app nunca carga el `.env.yaml` del repositorio.
cd "$work"
MONGODB_URI="mongodb://127.0.0.1:$mongo_port" PYTHONPATH="$repo" "$py" "$repo/scripts/visual_sweep.py" \
  --demo "mongodb://127.0.0.1:$mongo_port" "${DEMO_PORT:-8765}"
