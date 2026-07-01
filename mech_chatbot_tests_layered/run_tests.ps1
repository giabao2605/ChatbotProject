# Chay bo test theo tang tren Windows PowerShell.
# Dat thu muc nay TRONG repo (cung cap voi src\). Chay tu ben trong thu muc nay.

$ErrorActionPreference = "Stop"

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " TANG KHONG CAN HA TANG (chay duoc ngay)" -ForegroundColor Cyan
Write-Host " L4 RBAC + L5 guardrail + L1 sanitize + L3 logic" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
pytest -m "unit or security" -v

Write-Host ""
Write-Host "== De chay cac tang CAN HA TANG, bo comment cac dong duoi ==" -ForegroundColor Yellow
# --- L1 (DB) / L2 (Qdrant) / L6 (Server) : CHI tro toi STAGING CLONE ---
# $env:RUN_DB_TESTS = "1"
# $env:RUN_QDRANT_TESTS = "1"
# $env:QDRANT_URL = "http://localhost:6333"
# $env:QDRANT_API_KEY = "<staging-key>"
# $env:RAG_SERVER_URL = "http://localhost:8100"
# pytest -m "integration" -v

# --- Do coverage de lo cho chua test ---
# pytest -m "unit or security" --cov=mech_chatbot --cov-report=term-missing
