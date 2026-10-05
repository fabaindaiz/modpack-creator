#!/bin/bash
# Detiene los contenedores. Nunca agregar -v: borraría el volumen con los datos de MongoDB.
set -euo pipefail
cd "$(dirname "$0")/.."
# --profile admin: `down` sin el perfil deja arriba a mongo-express si se levantó a pedido.
docker compose -f docker/docker-compose.yaml --profile admin down
