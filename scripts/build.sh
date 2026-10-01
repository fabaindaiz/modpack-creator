#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
docker build -t modpack-creator-api:latest -f docker/Dockerfile .
