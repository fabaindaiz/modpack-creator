#!/bin/bash
# El gate local con los tests de base (i-7985ee-ade111): levanta un MongoDB descartable en
# 127.0.0.1:27018, corre ./scripts/check.sh contra él y lo borra al salir. Misma versión que CI.
set -euo pipefail
cd "$(dirname "$0")/.."
name="modpack-test-mongo-$$"
# La caché de WiredTiger, acotada: con la de por defecto, mongod muere a mitad de la corrida en una
# VM de 2 GiB (la de podman en macOS) y eso se ve como fallos de conexión de los tests.
docker run -d --rm --name "$name" -p 127.0.0.1:27018:27017 docker.io/library/mongo:8.0 --wiredTigerCacheSizeGB 0.25 >/dev/null
# `|| true`: si el contenedor ya no está, el código de salida sigue siendo el de check.sh.
trap 'docker stop "$name" >/dev/null 2>&1 || true' EXIT
until docker exec "$name" mongosh --quiet --eval 'db.adminCommand("ping")' >/dev/null 2>&1; do sleep 1; done
TEST_MONGODB_URI=mongodb://127.0.0.1:27018 ./scripts/check.sh
