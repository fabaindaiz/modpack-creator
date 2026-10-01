#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
git pull
./scripts/build.sh
./scripts/run.sh
