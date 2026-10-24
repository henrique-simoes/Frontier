#!/usr/bin/env bash
# Daily entry point: loads .env (API keys), then runs the pipeline.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then set -a; . ./.env; set +a; fi
exec .venv/bin/python -m radar run
