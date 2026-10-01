#!/bin/bash
# El gate local con los tests de base (i-7985ee-ade111): levanta un MongoDB descartable en
# 127.0.0.1:27018, corre ./scripts/check.sh contra él y lo borra al salir. Misma versión que CI.
set -euo pipefail
cd "$(dirname "$0")/.."
name="modpack-test-mongo-$$"
docker run -d --rm --name "$name" -p 127.0.0.1:27018:27017 docker.io/library/mongo:8.0 >/dev/null
trap 'docker stop "$name" >/dev/null' EXIT
until docker exec "$name" mongosh --quiet --eval 'db.adminCommand("ping")' >/dev/null 2>&1; do sleep 1; done
TEST_MONGODB_URI=mongodb://127.0.0.1:27018 ./scripts/check.sh
