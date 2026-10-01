#!/bin/bash
# Detiene los contenedores. Nunca agregar -v: borraría el volumen con los datos de MongoDB.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose -f docker/docker-compose.yaml down
