# Mech Chatbot — Nền tảng RAG đa phòng ban

An internal **Retrieval-Augmented Generation (RAG)** and knowledge-management platform for 14 department profiles. It classifies and ingests office or technical documents, enforces RBAC, department, site, clearance and serving-state policies, then returns evidence-backed answers with source provenance.

## Vận hành Windows production

Xem [hướng dẫn vận hành chính](docs/windows-service-operations.md) trước khi cập nhật
hoặc khởi động lại web. Candidate này dùng `backend/src` và `frontend/dist`.
Checkout production vẫn giữ cấu trúc cũ và môi trường `chat_env` hiện hành;
không cập nhật hoặc build đè nó trong đợt sắp xếp repo. Mã launcher nằm tại
`scripts/windows_service`; mã mới trong Git không có nghĩa service đã chuyển.
Đối chiếu command của Windows Service với hồ sơ phát hành để biết bản đang chạy.

Các hướng dẫn dev và demo bên dưới phục vụ môi trường riêng. Đặc biệt,
`scripts/ops/start_demo_lan.ps1` có chạy migration, không dùng làm lệnh restart
production. Không thử dependency hoặc build đè frontend tại checkout đang phục vụ.

---

## Key Features

| Feature | Description |
|---|---|
| **Domain-Aware Document Processing** | Documents are automatically classified into domains (`co_khi`, `ky_thuat`, `ke_toan`, `nhan_su`, `chung`) based on content. Each domain has its own extractor and quality-scoring strategy, configured centrally in `domain_registry.py`. |
| **Structured Vision OCR** | Uses an OpenAI-compatible Vision model to extract technical tables and Bills of Materials into a strict schema (`BangKeVatTu`), with a disk-based result cache (`vision_cache.py`) to avoid redundant API calls. |
| **Persistent FastAPI RAG Backend** | A high-performance API server (`rag_server.py`) that loads embedding/retrieval models once into memory, ensuring low latency and controlled concurrency (`MAX_CONCURRENT_RAG`). Supports auto-dispatch between subprocess mode and HTTP server mode. |
| **Asynchronous Ingestion Pipeline** | A background worker (`ingestion_worker.py`) handles PDF processing, OCR, and embedding generation via a managed job queue (`IngestionJobs`). Supports PDF, Word, Excel, CSV, plain text, Markdown, and images. |
| **RBAC + Department + Security-Level Access Control** | Named roles (Admin, Reviewer, Uploader, Viewer), per-user department scoping (`UserDepartments`), and per-user security clearance (`UserSecurityClearance`: `public` / `internal` / `confidential`). Access policy centralized in `auth/security_policy.py`. |
| **Access Request Workflow** | Users blocked by RBAC or security clearance can submit an access request (`AccessRequests` table). Admins/Reviewers approve or reject requests with notes directly from the UI. Approved requests apply the new clearance/department immediately. |
| **Permission Grant History & Revocation** | Full grant/revoke audit trail stored in `AuditLog`. Admins can lower clearance levels or remove department access at any time from the Access page. |
| **Rate Limiting** | Login rate limiting with lockout to prevent brute-force attacks (`auth/rate_limit.py`). |
| **Anti-Hallucination Guardrails** | A strict evidence-based verification layer (evidence gate) refuses to answer when quantitative data (fabrication time, costs, quantities) is absent from retrieved context. Reasons are logged per trace. |
| **HyDE (Hypothetical Document Embedding)** | Automatically activated for short or ambiguous questions to expand retrieval context before hybrid search. |
| **Voyage Reranking** | Voyage `rerank-2.5-lite` reranks retrieved chunks before generation and falls back to deterministic fusion ordering if unavailable. Enabling it sends the query and eligible candidate chunks to Voyage. |
| **Unified Dictionaries** | Reviewers manage the material dictionary and the per-domain term/synonym dictionary from one tabbed UI. `MaterialDictionary`, `MaterialSynonym`, and `DomainGlossary` remain separate because they serve different runtime semantics. |
| **Entity Resolver** | `rag/entity_resolver.py` normalizes material names and product codes before vector lookup. |
| **Intelligent Chitchat Handling** | `rag/chitchat.py` distinguishes casual conversation from technical queries, with bilingual (Vietnamese/English) responses. |
| **Sensitive Content Scanner** | `ingestion/sensitive_scanner.py` automatically detects sensitive information in documents during ingestion. |
| **Unified Document Catalogue** | One document catalogue provides mutually exclusive tabs for effective, expired, expiring-soon, and needs-review documents. Viewer access remains restricted by department, site, security clearance, and serving state. |
| **Explainable Ingest Quality Gate** | Ingest quality uses extraction coverage, chunk output, metadata/classification, and domain signals. Scores `85–100` pass to review, `60–84` require manual review, and lower scores or hard failures are blocked with reason codes. |
| **Role-Aware Dashboard** | Dashboard groups document lifecycle, ingestion, review, usage, and rollout metrics. Backend RBAC decides which groups and records each role may see. |
| **Observability Dashboard** | Every RAG request is persisted to `RagTraceSummary` (cost, token counts, per-step latency, refusal reason). The Observability page shows cost/token breakdown by department, daily trends, per-step latency charts, and top costly queries — all stored locally, no external telemetry. |
| **Answer Source Tracking** | Each chat answer records which document versions and pages were used (`AnswerSources` table), enabling full traceability. |
| **Visual Citations** | Provides source page images in the chat interface so users can verify the origin of every answer. |
| **Like / Dislike Feedback Loop** | Users rate each answer. Weighted quality scores (by role, time-decayed) are computed per document. Reviewers classify disliked answers by failure type (wrong version, retrieval miss, OCR error, hallucination, etc.) and promote correct answers to the golden set. |
| **Golden Answer Set** | Curated Q&A pairs used for offline evaluation and as a regression baseline (`GoldenAnswers` table). Reviewers can promote any classified feedback item to the golden set from the UI. |
| **Regression Test Suite** | Reviewers manage a set of regression questions with expected DocIDs and keywords. One-click batch run checks the current RAG pipeline against these expectations and stores pass/fail results. |
| **Orphan Cleanup** | Maintenance tool to purge dangling feedback records and golden answers referencing deleted documents or chat sessions. |
| **Document Quality Ranking** | Computed score per document from like/dislike signals (role-weighted, time-decayed). Low-scoring documents surface to reviewers automatically. |
| **Bilingual UI (Vi/En)** | The full interface and all system messages support Vietnamese and English via a centralized `i18n.py` translation layer (`t()` function). Language is toggled from the sidebar. |
| **Fail-Fast Config Validation** | `config/validate.py` validates all required environment variables at startup and raises `ConfigError` immediately with a clear error list, rather than failing silently later. Secrets are masked in all log output. |
| **Access Audit Logging** | All access to `confidential`-level documents and permission changes are recorded in `AuditLog` for compliance. |
| **RAGAS Continuous Evaluation (CI)** | A GitHub Actions workflow runs automated RAG quality evaluation weekly (or on demand), gates on regression tolerance, and uploads `ragas_report.md` as a build artifact. |
| **LAN Demo Operations** | The Windows demo script applies migrations, starts the local RAG server and exposes the browser application on the LAN. |

---

## Technology Stack

| Component | Technology |
|---|---|
| Language | Python 3.11+ |
| Frontend / UI | Vue 3 + Vite + PrimeVue served by FastAPI |
| API Backend | [FastAPI](https://fastapi.tiangolo.com/) + Uvicorn |
| LLM & Vision | OpenAI-compatible endpoint (ProxyLLM) — configured via `GPT_MODEL_NAME`, `GPT_VISION_MODEL_NAME` |
| Embedding Model | `BAAI/bge-m3` (sentence-transformers, 1024 dims) + BM25 hybrid search |
| Vector Database | [Qdrant Cloud](https://qdrant.tech/) |
| Relational Database | Microsoft SQL Server |
| CI / Evaluation | GitHub Actions + RAGAS |
| Key Libraries | `PyMuPDF`, `pdfplumber`, `LangChain`, `SQLAlchemy`, `pyodbc`, `underthesea`, `bcrypt`, `tenacity`, `Pillow` |

---

## Project Structure

| Directory | Purpose |
|---|---|
| `backend/src/mech_chatbot/` | Python API, RAG, ingestion, domain and governance code |
| `backend/tests/` | Main backend suite, architecture checks and fixtures |
| `backend/tests_layered/` | Independent layered suite and historical runner compatibility |
| `backend/requirements/` | Runtime, test, security and optional dependency files; pinned versions unchanged |
| `frontend/` | Vue application, frontend tests and Vite build |
| `scripts/` | Development, evaluation, diagnostics and Windows service tools |
| `database/` | Schema, migrations and seed scripts |
| `docs/` | Operating guides, architecture decisions and the original architecture document |
| `data/`, `src/data/`, `eval/` | Existing corpus, page images, caches and evaluation inputs; paths preserved |
| `reports/` | Existing review and release evidence; historical hashes preserved |
| `.local/` | Local runs, temporary files and isolated environments; not versioned |

`run_server.py` and `run_worker.py` remain root launchers. Root `pytest.ini`
collects `backend/tests`; the layered suite uses its own configuration.
The Python package is still named `mech_chatbot`; HTTP APIs and database schemas
are unchanged. See [restructure evidence and rollback](docs/repository-restructure-20261030.md).

---

## Engineering workflow

Engineering skills from `mattpocock/skills` are installed as personal skills in
`~/.agents/skills/`. Repository-specific workflow settings remain in `CODEX.md`.

- GitHub Issues is the tracker for implementation work.
- `docs/agents/triage-labels.md` defines the shared triage vocabulary.
- `docs/agents/domain.md` explains how agents consume `CONTEXT.md` and ADRs.

The skill files are managed outside this repository and shared across projects.

---

## Database Schema (Key Tables)

| Table | Purpose |
|---|---|
| `TaiLieu` | Core document registry (filename, domain, department, site, security level, lifecycle dates, version) |
| `BangKeVatTu` | Extracted Bill of Materials rows (structured, schema-enforced) |
| `DocumentPages` | Per-page rendered image paths for visual citations |
| `DocumentAttributes` | Flexible key-value metadata per document |
| `IngestionJobs` | Background ingestion job queue and status |
| `LichSuChat` | Chat history with per-session grouping and feedback scores |
| `AnswerSources` | Document versions/pages used to generate each answer |
| `FeedbackReview` | Disliked answers pending reviewer classification |
| `GoldenAnswers` | Curated correct Q&A pairs for evaluation |
| `RegressionQuestions` | Regression test questions with expected DocIDs/keywords |
| `RegressionRuns` | Batch regression execution results |
| `Users` / `Roles` / `UserRoles` | User accounts, role definitions, role assignments |
| `Departments` / `UserDepartments` | Department registry and per-user department access |
| `UserSecurityClearance` | Per-user maximum security clearance level |
| `AccessRequests` | Access elevation requests (pending / approved / rejected) |
| `AuditLog` | Immutable audit trail for permission changes and confidential access |
| `DomainGlossary` | Per-domain synonym and abbreviation dictionary |
| `RagTraceSummary` | Per-request observability: cost, tokens, per-step latency, refusal |
| `SemanticCache` | Cached RAG answers keyed by embedding similarity and RBAC scope |
| `AppSettings` | Key-value application configuration stored in DB |
| `PublicationOutbox` | Outbox queue for document publication events |
| `ExternalAiProviders` | External AI provider profiles and routing configuration |
| `KnowledgeGovernance` | Knowledge governance policies and serving epoch tracking |
| `DepartmentRollout` | Department-level feature rollout gates |
| `CitationProvenance` | Citation provenance and chat evidence tracking |

---

## Getting Started

### 1. Prerequisites

- Python 3.11+
- PowerShell on Windows for the simplest LAN demo workflow
- Microsoft SQL Server + ODBC Driver (for `pyodbc`)
- A Qdrant Cloud account (URL + API key)
- An OpenRouter account and API key for text and vision

### 2. Configure Environment

Copy the fail-closed example to a local `.env` file, then replace the blank
secret values. Never commit the resulting `.env` file.

```powershell
Copy-Item .env.example .env
```

Project provider configuration is shared: keep the OpenRouter key and both model
names in the primary repository root `.env`. Text LLM and vision/OCR use the same
`OPENROUTER_API_KEY`. A configured `OPENROUTER_BASE_URL` selects that key exclusively;
an empty key never falls back to a legacy provider key. Restart existing processes
after a change. Environment variables explicitly supplied by launchers take precedence.

Git branches contain independent code snapshots. Branches used for new work must
include the OpenRouter adapter/settings changes; environment configuration alone
cannot migrate an older adapter. Historical rollout snapshots remain historical.
Production also requires the managed `openrouter` profile with approved surfaces
and a valid review expiry; no automatic policy bypass is installed.

The important runtime values are:

```env
# Application security and external processing
APP_ENV=production
APP_SESSION_SECRET=<long-random-secret>
APP_COOKIE_SECURE=true
APP_TRUSTED_HOSTS=localhost,127.0.0.1,<deployed-hostname-or-ip>
EXTERNAL_AI_LOCAL_DEVELOPMENT=false
EXTERNAL_PROCESSING_POLICY=internal_only

# LLM / Vision
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_API_KEY=<your-openrouter-api-key>
GPT_MODEL_NAME=openai/gpt-5.6-luna
GPT_VISION_MODEL_NAME=openai/gpt-5.6-luna
GPT_TEMPERATURE=0
GPT_MAX_OUTPUT_TOKENS=8000
GPT_VISION_MAX_OUTPUT_TOKENS=16000
GPT_TIMEOUT_SECONDS=300
GPT_VISION_JPEG_QUALITY=95

# Voyage Reranking
# Reranking sends the question and candidate chunks to Voyage AI.
VOYAGE_API_KEY=<your-voyage-api-key>
USE_VOYAGE_RERANK=true
VOYAGE_RERANK_MODEL=rerank-2.5-lite
VOYAGE_RERANK_TIMEOUT_SECONDS=15

# Vector Database
QDRANT_URL=<your-qdrant-cloud-url>
QDRANT_API_KEY=<your-qdrant-api-key>
QDRANT_SEARCH_TIMEOUT_SECONDS=10

# Embedding
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_DEVICE=cpu
EMBEDDING_DIM=1024
EMBEDDING_CHUNK_SIZE=600
EMBEDDING_CHUNK_OVERLAP=80

# SQL Server (Windows Auth)
SQL_SERVER=localhost\SQLEXPRESS
SQL_DATABASE=Mech_Chatbot_DB
SQL_TRUSTED_CONNECTION=true
# SQL Server (Username/Password — for remote/CI)
# SQL_USERNAME=<user>
# SQL_PASSWORD=<pass>

# RAG Server & Advanced Routing
RAG_SERVER_URL=http://localhost:8100
RAG_SERVER_PORT=8100
MAX_CONCURRENT_RAG=4
RAG_WORKER_TIMEOUT=240
RAG_REQUIRE_SERVICE_AUTH=true
RAG_SERVICE_TOKEN=<long-random-shared-secret>

SEMANTIC_CACHE_ENABLED=true
SEMANTIC_CACHE_SIM_THRESHOLD=0.93
SEMANTIC_CACHE_TTL_HOURS=24
ENABLE_CONV_STATE=true
ENABLE_HISTORY_SUMMARY=true
ENABLE_CONTEXTUAL_CHUNK=true
SEMANTIC_ROUTER_ENABLED=true
SEMANTIC_ROUTER_SIM_THRESHOLD=0.55
SEMANTIC_ROUTER_MARGIN=0.04

# Chat Bridge (app_server <-> rag_server shared secret)
CHAT_BRIDGE_SECRET=<long-random-hex-string>

# Authorization defaults
RBAC_STRICT_SITE_FILTER=true
KNOWLEDGE_ALLOW_ADMIN_APPROVAL_OVERRIDE=false
KNOWLEDGE_ALLOW_ADMIN_METADATA_OVERRIDE=false

# Governed RAG features stay disabled by default
RAG_CRAG_ENABLED=false
RAG_CLAIM_REPAIR_ENABLED=false
RAG_GROUNDED_MATH_ENABLED=false
RAG_LATE_INTERACTION_ENABLED=false
RAG_LATE_ENCODER_READY=false
RAG_QUERY_DECOMPOSITION_ENABLED=false
RAG_GRAPH_RETRIEVAL_ENABLED=false
RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED=false
RAG_ACTIVATION_SCOPE=default_rollout
RAG_ACTIVATION_BUNDLE_PATH=
RAG_ACTIVATION_BUNDLE_SHA256=

# Strict Modes
STRICT_INGEST_REQUIRE_VISION=true
STRICT_ANSWER_MODE=true

# PDF rendering
PDF_RENDER_DPI=300
METADATA_TEXT_LIMIT=20000
```

`EXTERNAL_PROCESSING_POLICY=all_external` is an explicit opt-in. Use it only
with an approved provider profile, privacy-safe audit metadata, and the
required governance review. Controlled RAG flags also require their matching
activation bundle and release decision; code, tests, or a successful provider
smoke do not authorize enabling them.

Use a distinct random `APP_SESSION_SECRET`; do not reuse the RAG service or
chat bridge secret. Keep `APP_COOKIE_SECURE=true` behind HTTPS. A local or LAN
HTTP-only demo may set it to `false`, but that exception must not be copied to
an HTTPS deployment. Set `APP_TRUSTED_HOSTS` to the exact hostnames or IP
addresses users put in the browser; wildcard hosts are rejected in production.

For GitHub Actions CI, configure these repository **Secrets**: `QDRANT_URL`, `QDRANT_API_KEY`, `SQL_SERVER`, `SQL_DATABASE`, `SQL_USERNAME`, `SQL_PASSWORD`, `OPENAI_API_KEY`.

> **Config validation:** The RAG server and workers call
> `assert_config_valid()` at startup. Browser-session security fails closed
> when `APP_SESSION_SECRET` is missing. Configuration errors never print
> secrets in plain text.

### 3. Database Setup

```bash
# Step 1: Create the base schema
#   Run: database/schema/01_baseline.sql on your SQL Server instance

# Step 2: Apply all discovered versioned migrations in order
python scripts/migrations/migrate.py
# or run each file in database/migrations/ manually

# Step 3: Initialize Qdrant collections
python scripts/create_qdrant_indexes.py

# Step 4: Verify migration, Qdrant, all-off activation and account safety
python scripts/ops/production_preflight.py --skip-health
```

> **Security note:** Migrations seed example accounts (`admin`, `reviewer1`, `viewer1`, `uploader1`). Keep them only for local bootstrap and make them inactive before any production deployment. The preflight reports only the active account count and fails until those seeded accounts are inactive; it never changes or deletes an account.

### 4. Running the Application

**Option A: Windows LAN demo (recommended for the current pilot)**

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\ops\start_demo_lan.ps1
```

The script applies every discovered pending migration, starts the RAG server on
`127.0.0.1:8100`, starts a single supervised ingestion worker, and exposes the
browser application on the configured LAN address. It waits for the worker
readiness marker before reporting the demo as ready. The output includes the
RAG, worker supervisor, and app process IDs; stop the worker supervisor tree
with `taskkill /PID <worker-supervisor-pid> /T /F`, then stop the RAG and app
processes before rerunning the launcher.

**Option B: Local Development**

```bash
git clone https://github.com/giabao2605/ChatbotProject.git
cd ChatbotProject
pip install -r backend/requirements/requirements.txt
```

Start each service in a separate terminal:

```bash
# Terminal 1 — RAG API server
python run_server.py

# Terminal 2 — Ingestion worker
$env:PYTHONPATH="backend/src"; python run_worker.py

# Terminal 3 — Vue UI + browser-facing FastAPI
$env:PYTHONPATH="backend/src"; $env:APP_SERVER_HOST="0.0.0.0"; $env:APP_SERVER_PORT="8080"; python -m mech_chatbot.api.app_server
```

> Alternatively, run with `PYTHONPATH=backend/src` explicitly:
> ```bash
> PYTHONPATH=backend/src python -m mech_chatbot.api.rag_server
> PYTHONPATH=backend/src python -m mech_chatbot.workers.ingestion_worker
> PYTHONPATH=backend/src APP_SERVER_HOST=0.0.0.0 APP_SERVER_PORT=8080 python -m mech_chatbot.api.app_server
> ```

Run live browser E2E only against an isolated candidate with its own SQL/Qdrant
data and test credentials. Do not copy production `.env` or credentials. Verify
the candidate identity before login/chat; the current Windows services use
ports 8080/8100, so use separate ports for this checkout:

```powershell
$env:E2E_BASE_URL="http://127.0.0.1:18080"
$env:E2E_RAG_BASE_URL="http://127.0.0.1:18100"
$env:DEV_API_TARGET="http://127.0.0.1:18180"
$env:E2E_CREDENTIALS_PATH="C:\path-to-isolated-test-credentials.json"
$env:E2E_BROWSER_CHANNEL="chrome"
npm --prefix frontend run test:e2e
```

`E2E_BASE_URL` is required. The live RAG health check also requires
`E2E_RAG_BASE_URL`. Fixture-only browser tests can use a frontend preview on
18080 with intercepted API responses; passing them does not prove live SQL,
provider, login or ingestion readiness. `DEV_API_TARGET` controls the Vite API
proxy and must point to the separate candidate app API, not the RAG API.


Omit `E2E_BROWSER_CHANNEL` after installing bundled Chromium with
`npx playwright install chromium` from `frontend`. The E2E runner reads demo
credentials only inside the test process and keeps traces/videos off while
login and chat requests are in flight.

---

## Application Pages

Access pages via the Vue sidebar (visibility depends on your role and clearance):

| Page | Description | Required Role |
|---|---|---|
| **Chatbot Q&A** | Ask technical questions; get evidence-based answers with visual citations, like/dislike feedback | All |
| **Ingestion Queue** | Monitor background document processing jobs and status | Uploader, Admin |
| **Document Catalogue** | Browse effective documents; Reviewers manage expired, expiring-soon, and needs-review tabs | All; management tabs require Reviewer/Admin |
| **Upload** | Upload PDF/Word/Excel/images with metadata, security level, and site tagging | Uploader, Admin |
| **Organization** | Department/site/branch management and organizational structure | Admin |
| **Users** | Manage accounts, roles, department access, and security clearance | Admin |
| **Access Requests** | Submit access requests; Reviewers approve/reject; Admins revoke and view history | All (role-filtered) |
| **Audit** | Confidential document access log and permission change history | Admin |
| **Analytics** | System usage statistics and trends | Admin, Reviewer |
| **Dashboard** | Role-aware lifecycle, ingestion, review, usage, and rollout overview | All; content is role-filtered |
| **Dictionary** | Material dictionary and domain terms/synonyms in two tabs | Reviewer, Admin |
| **Observability** | RAG cost, token, latency, and refusal analytics per department | Admin |
| **Settings** | Interface language toggle (Vi/En) and personal preferences | All |
| **Feedback** | Classify disliked answers, manage golden set, document quality ranking | Reviewer, Admin |
| **Document Review** | Review and approve pending documents with annotation support | Reviewer, Admin |
| **Quality** | Document quality ranking and scoring dashboard | Reviewer, Admin |
| **Regression** | Manage and run regression test suites against the RAG pipeline | Reviewer, Admin |
| **Help** | System user guide | All |

---

## RAG Pipeline (Request Flow)

```
User question
    |
    v
Interaction Router
    |-- safety/meta/chitchat routes can return early
    v
Semantic Cache Lookup
    |-- cache hit can return early
    v
Context Rewrite + Conversation Anchor
    |-- decontextualize follow-up questions and resolve active document refs
    v
Intent Extraction (LLM / regex fallback)
    |-- detect part IDs, BOM queries, language, version policy
    v
Domain Glossary Expansion (TTL-cached from DB)
    |-- synonyms and abbreviations added to query
    v
HyDE (Hypothetical Document Embedding)
    |-- activated for short or ambiguous questions
    v
Hybrid Search (Dense + BM25) on Qdrant
    |-- RBAC filter: department + security clearance + site + lifecycle status
    v
Variant Disambiguation
    |-- asks the user to choose when multiple variants match
    v
Voyage Reranking
    |-- filter top-N by relevance score
    v
Evidence Gate (LLM)
    |-- verify context is sufficient to answer; else refusal
    v
Answer Generation (LLM)
    |-- response language: Vi / En
    v
Answer + Visual Citations + Source Tracking
    |-- saved to LichSuChat, AnswerSources, RagTraceSummary
    v
Audit log (confidential document access if applicable)
```

---

## CI / Continuous Evaluation

The project includes a GitHub Actions workflow (`.github/workflows/ragas_eval.yml`):

- **Trigger:** Every Sunday at 18:00 UTC, or manually via `workflow_dispatch`
- **What it does:** Runs `scripts/eval/run_ragas_eval.py` against the live Qdrant + SQL + LLM stack using the golden question set
- **Output:** Uploads `reports/ragas_report.md` as a build artifact for regression tracking
- **Regression tolerance:** `RAGAS_TOLERANCE=0.05` (5% allowed degradation)

---

## Testing

### Wave 1-4 demo bootstrap

The demo bootstrap is opt-in and tags every generated record with
`demo-wave-v1`. Credentials are generated locally in
`.local/demo-wave-credentials.json`; that path is ignored by Git.

```powershell
.\chat_env\Scripts\python.exe scripts\demo_wave\bootstrap_demo_wave_data.py all
.\chat_env\Scripts\python.exe scripts\demo_wave\ingest_demo_corpus.py
.\chat_env\Scripts\python.exe scripts\eval\run_demo_wave_gate.py --mode full --record-gates
```

The generator creates 84 lifecycle fixtures and 1,050 evaluation cases
(75 for each of the 14 configured departments). A retrieval/debug run never
writes a rollout gate; `--record-gates` is accepted only in full mode.

```bash
pip install -r backend/requirements/requirements-test.txt

# Fast local suite (skips integration and eval tests)
pytest -m "not integration and not eval"

# Full pytest collection. Integration/eval tests are skipped unless their
# required environment variables are enabled in backend/tests/conftest.py.
pytest

# Run by layer
pytest backend/tests/
pytest mech_chatbot_tests_layered/

# Manual RAG quality evaluation
PYTHONPATH=backend/src python scripts/eval/run_ragas_eval.py

# Manual evaluation with detailed output
PYTHONPATH=backend/src python scripts/eval/run_eval.py
```

Evaluation reports are stored in the `reports/` directory.

### Department rollout waves

- Wave 1: `Technical`, `HR`, `Purchasing` (`pilot`).
- Wave 2: `Warehouse`, `Accountant`, `Sales`, `Planning` (`planned`).
- Wave 3: `Production`, `Maintenance`, `QualityControl`, `ISO` (`planned`).
- Wave 4: `Molding`, `HSE_5S`, `IT` (`planned`; the fourth slot is empty).

The checked-in demo bootstrap provisions a demo corpus, demo governance and a
75-question evaluation set for every configured department. It is useful for
code verification only; rollout status remains explicit and is never promoted
automatically by the bootstrap or evaluation runner.

For a real rollout, replace demo ownership, corpus and questions with approved
department data, then use the readiness API and the relevant checklist in
`docs/wave*-rollout-checklist.md`. Wave transitions also require earlier waves
to be active where applicable.

The templates in `scripts/eval/templates/wave1` through `wave4` describe the
expected per-department scenarios. The demo-runner's reports are ignored by
Git and are written under `reports/demo_wave/`.

After the real manifests for a wave have been reviewed, validate them without
creating or recording a gate. The command below is the Wave 2 example; Wave 1,
Wave 3 and Wave 4 commands are documented in their respective template
directories:

```powershell
$env:PYTHONPATH="backend/src"
python scripts/eval/pilot_rollout_gate.py reports/wave2-evaluation.jsonl `
  --expected-departments Warehouse,Accountant,Sales,Planning `
  --expected-department-count 4 `
  --minimum 75
```

Platform admins can inspect the backend-computed prerequisites from an
authenticated browser session. Open the browser developer console on the app
and run:

```javascript
fetch("/api/catalog/rollout/readiness", { credentials: "include" })
  .then((response) => response.json())
  .then((result) => console.table(result.departments));
```

The frontend does not calculate or override readiness. Treat
`missing_prerequisites` from this response as the authoritative checklist.

SQL Server, Qdrant, and live RAG evaluation tests are opt-in. Enable them only
against explicit test services with `RUN_DB_TESTS=1`, `RUN_QDRANT_TESTS=1`, or
`RUN_EVAL_TESTS=1`; the default suite skips those tests.

Verify all SQL migrations on a disposable database before deployment:

```powershell
.\chat_env\Scripts\python.exe scripts\migrations\verify_clean_migration.py `
  --database Mech_Chatbot_Test_Migrations --recreate
```

---

## Useful Scripts

| Script | Purpose |
|---|---|
| `scripts/create_qdrant_indexes.py` | Create or recreate Qdrant collections |
| `scripts/nap_them_file.py` | Manually ingest additional documents |
| `scripts/route_dashboard.py` | Analyze dashboard routes |
| `scripts/migrations/migrate.py` | Run pending SQL migrations in order |
| `scripts/migrations/backfill_qdrant_governance_metadata.py` | Backfill governance metadata in Qdrant |
| `scripts/migrations/backfill_qdrant_servable.py` | Backfill servable flag in Qdrant |
| `scripts/migrations/verify_clean_migration.py` | Verify migration integrity |
| `scripts/eval/run_ragas_eval.py` | Run RAGAS quality evaluation (also used by CI) |
| `scripts/eval/run_eval.py` | Manual evaluation with detailed output |
| `scripts/eval/benchmark_rag_concurrency.py` | RAG concurrency load testing |
| `scripts/eval/pilot_rollout_gate.py` | Department rollout gate evaluation |
| `scripts/eval/run_demo_wave_gate.py` | Run or re-score the 14-department demo evaluation gate |
| `scripts/demo_wave/bootstrap_demo_wave_data.py` | Create local demo users, governance assignments and evaluation cases |
| `scripts/demo_wave/ingest_demo_corpus.py` | Ingest and publish the generated demo corpus |
| `scripts/demo_wave/cleanup_demo_wave_data.py` | Remove only demo-wave records and artifacts |
| `scripts/eval_semantic_router.py` | Semantic router evaluation script |
| `scripts/diagnostics/check_qdrant_count.py` | Verify document count in Qdrant |
| `scripts/diagnostics/check_qdrant_schema.py` | Inspect Qdrant collection schema |
| `scripts/diagnostics/check_image_summary_coverage.py` | Check Vision OCR coverage per document |
| `scripts/ops/backup_system.py` | Back up system state |
| `scripts/ops/loadtest_lan.py` | LAN load testing tool |
| `scripts/ops/start_demo_lan.ps1` | Start all services for LAN demo |
| `scripts/admin/hash_pass.py` | Hash passwords for manual account seeding |
| `scripts/danger_ops/empty_bag.py` | Purge documents from Qdrant |
| `scripts/danger_ops/reconcile_sql_qdrant.py` | Reconcile SQL ↔ Qdrant state |
| `scripts/danger_ops/reset_and_create_dev_db.sql` | Full dev database reset |

---

## Contributing

Contributions, bug reports, and feature requests are welcome. Please open an issue on the GitHub repository.

## License

This project is proprietary. All rights reserved.
