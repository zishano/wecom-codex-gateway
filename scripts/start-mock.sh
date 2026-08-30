#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

export MOCK_WECOM=true
export MOCK_CODEX=true
export DEV_API_TOKEN="local-development-token"
export STATE_DB="./data/mock-gateway.db"

exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8787

