#!/usr/bin/env bash
# One-time setup: virtualenv + dependencies.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"
"$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
[ -f .env ] || cp .env.example .env
chmod 600 .env
if [ -t 0 ]; then
  .venv/bin/python -m radar setup        # walks through each API key, validates it, offers the first run
else
  echo "Ready. Next: .venv/bin/python -m radar setup"
fi
