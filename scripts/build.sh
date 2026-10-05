#!/bin/bash
# Construye la imagen con dos etiquetas: :latest, la que levanta el compose, y :<commit>, para
# volver atrás sin recompilar (`docker tag modpack-creator-api:<commit> modpack-creator-api:latest`
# y `./scripts/run.sh`). Un árbol con cambios sin commit lleva `-dirty`: esa imagen no es un commit.
set -euo pipefail
cd "$(dirname "$0")/.."
commit="$(git rev-parse --short HEAD)"
git diff --quiet HEAD -- || commit="$commit-dirty"
version="$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml)"
docker build -t modpack-creator-api:latest -t "modpack-creator-api:$commit" \
  --build-arg "VERSION=$version+$commit" -f docker/Dockerfile .
echo "imagen: modpack-creator-api:latest = modpack-creator-api:$commit ($version)" >&2
