#!/usr/bin/env bash
# Chay bo test theo tang (Linux/macOS). Dat thu muc nay TRONG repo (cung cap voi src/).
set -euo pipefail

echo "=================================================="
echo " TANG KHONG CAN HA TANG (chay duoc ngay)"
echo " L4 RBAC + L5 guardrail + L1 sanitize + L3 logic"
echo "=================================================="
pytest -m "unit or security" -v

echo
echo "== De chay cac tang CAN HA TANG, bo comment cac dong duoi (chi tro STAGING CLONE) =="
# export RUN_DB_TESTS=1
# export RUN_QDRANT_TESTS=1
# export QDRANT_URL=http://localhost:6333
# export QDRANT_API_KEY=<staging-key>
# export RAG_SERVER_URL=http://localhost:8100
# pytest -m integration -v

# pytest -m "unit or security" --cov=mech_chatbot --cov-report=term-missing
