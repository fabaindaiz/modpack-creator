#!/bin/bash
# El gate local con los tests de base (i-7985ee-ade111): levanta un MongoDB descartable en
# 127.0.0.1:27018, corre ./scripts/check.sh contra él y lo borra al salir. Misma versión que CI.
set -euo pipefail
cd "$(dirname "$0")/.."
name="modpack-test-mongo-$$"
# Otro mongod ya corriendo (la demo, un barrido, otra corrida): en la VM de 2 GiB de podman el kernel
# mata a uno de los dos a mitad de la corrida. Solo se avisa: con Docker de verdad suele caber.
# `docker ps --format` con `.Names` e `.Image` lo entienden docker y podman.
others="$(docker ps --format '{{.Names}} {{.Image}}' 2>/dev/null | awk '$2 ~ /(^|\/)mongo(:|$)/ {print $1}' || true)"
if [ -n "$others" ]; then
  echo "test-mongo.sh: AVISO: ya hay otro MongoDB corriendo ($(echo $others)); en la VM de podman" \
       "dos mongod no caben y uno muere a mitad de la corrida. Detenlo si los tests fallan por conexión." >&2
fi
# La caché de WiredTiger, acotada: con la de por defecto, mongod muere a mitad de la corrida en una
# VM de 2 GiB (la de podman en macOS) y eso se ve como fallos de conexión de los tests.
# Y los archivos de las colecciones borradas, cerrados a los 10 s y no a los 10 min: cada test crea y
# borra sus bases, y con miles abiertos mongod pasaba de 1,5 GB y la VM lo mataba (2026-10-09).
docker run -d --rm --name "$name" -p 127.0.0.1:27018:27017 docker.io/library/mongo:8.0 --wiredTigerCacheSizeGB 0.25 \
  --wiredTigerEngineConfigString "file_manager=(close_idle_time=10,close_scan_interval=10,close_handle_minimum=100)" \
  --setParameter tcmallocAggressiveMemoryDecommit=1 >/dev/null
# `|| true`: si el contenedor ya no está, el código de salida sigue siendo el de check.sh.
trap 'docker stop "$name" >/dev/null 2>&1 || true' EXIT
until docker exec "$name" mongosh --quiet --eval 'db.adminCommand("ping")' >/dev/null 2>&1; do sleep 1; done
TEST_MONGODB_URI=mongodb://127.0.0.1:27018 ./scripts/check.sh
