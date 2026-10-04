#!/bin/bash
# Levanta la API, MongoDB y mongo-express. La configuración es .env.yaml (montado de solo lectura);
# sin él la API no arranca, así que se comprueba antes.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env.yaml ] || { echo "run.sh: falta .env.yaml (copia .env.default y complétalo)" >&2; exit 1; }
docker compose -f docker/docker-compose.yaml up -d
