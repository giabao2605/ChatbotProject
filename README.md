# Mechanical & Multi-Department RAG Chatbot

An enterprise-grade **Retrieval-Augmented Generation (RAG)** platform built for the mechanical engineering domain and extended into a **multi-department document management system** (mechanical, technical, accounting, HR, and shared documents). The system automatically classifies PDF documents by domain, extracts structured data (Bills of Materials), enforces layered access control (RBAC + department + security clearance), and returns accurate, citation-backed answers without hallucination.

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
| **GPT Reranking** | LLM-based reranking filters retrieved chunks by relevance before generation (`USE_GPT_RERANK`, `GPT_RERANK_MAX_DOCS`). |
| **Domain Glossary** | Admins manage a per-domain synonym/abbreviation dictionary (`DomainGlossary` table, `glossary.py`). Changes take effect immediately — the RAG engine queries the glossary (with a short TTL cache) to expand queries and improve recall without code changes. |
| **Entity Resolver** | `rag/entity_resolver.py` normalizes material names and product codes before vector lookup. |
| **Intelligent Chitchat Handling** | `rag/chitchat.py` distinguishes casual conversation from technical queries, with bilingual (Vietnamese/English) responses. |
| **Sensitive Content Scanner** | `ingestion/sensitive_scanner.py` automatically detects sensitive information in documents during ingestion. |
| **Document Lifecycle Management** | Track effective date, expiry date, and periodic review deadlines per document. The lifecycle page (`lifecycle.py`) shows expired, expiring-soon, and needs-review documents. One-click "mark as reviewed" extends the next review date by 180 days. |
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
| **Docker Compose Ready** | A single command brings up the full stack (UI + API backend + ingestion worker). |

---

## Technology Stack

| Component | Technology |
|---|---|
| Language | Python 3.11 |
| Frontend / UI | [Streamlit](https://streamlit.io/) |
| API Backend | [FastAPI](https://fastapi.tiangolo.com/) + Uvicorn |
| LLM & Vision | OpenAI-compatible endpoint (ProxyLLM) — configured via `GPT_MODEL_NAME`, `GPT_VISION_MODEL_NAME` |
| Embedding Model | `BAAI/bge-m3` (sentence-transformers, 1024 dims) + BM25 hybrid search |
| Vector Database | [Qdrant Cloud](https://qdrant.tech/) |
| Relational Database | Microsoft SQL Server |
| CI / Evaluation | GitHub Actions + RAGAS |
| Key Libraries | `PyMuPDF`, `pdfplumber`, `LangChain`, `SQLAlchemy`, `pyodbc`, `underthesea`, `bcrypt`, `tenacity`, `Pillow` |

---

## Project Structure

```text
ChatBotProject/
├── .env                          # Environment variables (API keys, DB, model names)
├── Dockerfile                    # Python 3.11 image used by all services
├── docker/
│   └── docker-compose.yml        # Orchestration: UI + API server + Worker
├── run.py                        # Entry point: Streamlit UI
├── run_server.py                 # Launcher: FastAPI RAG server
├── run_worker.py                 # Launcher: Ingestion worker
├── requirements.txt              # Python dependencies
├── requirements.lock.txt         # Pinned dependencies
├── requirements-test.txt         # Test dependencies
├── pytest.ini                    # Test configuration
│
├── .github/
│   └── workflows/
│       ├── ragas_eval.yml        # CI: weekly RAGAS evaluation (or manual trigger)
│       └── tests.yml             # CI: automated test suite runner
│
├── components/
│   └── liquid_login/                     # Custom Streamlit UI component for login
│
├── database/
│   ├── schema/
│   │   └── 01_baseline.sql               # Base schema definitions
│   ├── seed/
│   │   ├── 01_roles.sql
│   │   ├── 02_dev_accounts.sql
│   │   └── 03_departments.sql
│   ├── data_migrations/
│   │   └── 0001_normalize_domain_values.sql
│   ├── migrations/                       # Versioned SQL migrations (Flyway-style, idempotent)
│   │   ├── V0001__backfill_clearance_safe_default.sql
│   │   ├── V0002__deactivate_legacy_stage_departments.sql
│   │   ├── V0003__add_filepath_to_tailieu.sql
│   │   ├── V0004__add_common_document_metadata.sql
│   │   ├── V0005__add_app_settings.sql
│   │   ├── V0006__department_status_archive_reassign.sql
│   │   ├── V0007__add_login_attempts.sql
│   │   ├── V0008__add_site_to_departments.sql
│   │   ├── V0009__normalize_phongban_sharing.sql
│   │   ├── V0010__access_requests.sql
│   │   ├── V0011__domain_glossary.sql
│   │   ├── V0012__rag_trace_summary.sql
│   │   ├── V0013__doc_lifecycle_review.sql
│   │   ├── V0014__semantic_cache.sql                 # Semantic cache tables
│   │   └── V0015__perf_index_userdepartments.sql     # Index for access checks
│   └── MIGRATIONS.md                     # Migration documentation
│
├── scripts/
│   ├── create_qdrant_indexes.py          # Initialize Qdrant Cloud collections
│   ├── nap_them_file.py                  # Manually ingest additional documents
│   ├── admin/
│   │   └── hash_pass.py                  # Hash passwords for manual account seeding
│   ├── diagnostics/
│   │   ├── check_image_summary_coverage.py
│   │   ├── check_qdrant_count.py
│   │   └── check_qdrant_schema.py
│   ├── route_dashboard.py                # Dashboard route analyzer
│   ├── eval/
│   │   ├── run_ragas_eval.py             # RAGAS evaluation entry point (used by CI)
│   │   ├── run_eval.py                   # Manual evaluation runner
│   │   ├── ragas_metrics.py              # RAGAS metric definitions
│   │   ├── evaluate_chatbot.py           # Chatbot evaluation harness
│   │   ├── eval_semantic_router.py       # Semantic router evaluation script
│   │   ├── golden_set.jsonl              # Full golden question set
│   │   └── golden_set_datagoc_real.jsonl # Real-data golden set
│   ├── migrations/
│   │   ├── migrate.py                    # Run pending SQL migrations in order
│   │   ├── migrate_qdrant_collection.py
│   │   ├── alter_ingestionjobs.py
│   │   ├── check_empty.py
│   │   └── check_qdrant.py
│   ├── ops/
│   │   └── backup_system.py              # System backup utility
│   └── danger_ops/
│       ├── empty_bag.py                  # Purge documents from Qdrant
│       ├── reconcile_sql_qdrant.py       # Reconcile SQL ↔ Qdrant state
│       └── reset_and_create_dev_db.sql   # Full dev database reset
│
├── tests/                                # Golden question sets and test fixtures
├── mech_chatbot_tests_layered/           # Layered tests (unit, integration, e2e)
├── reports/                              # RAG evaluation reports (incl. ragas_report.md)
├── data/
│   ├── raw/                              # Source PDF/document files
│   ├── processed/                        # Rendered page images
│   └── cache/                            # Vision OCR cache (not committed to git)
│
└── src/mech_chatbot/                     # Core application source code
    ├── ui/
    │   ├── app.py                        # Streamlit main router & sidebar nav
    │   ├── i18n.py                       # Centralized translation layer (t() function, Vi/En)
    │   ├── labels.py                     # Department/site display label helpers
    │   ├── metadata_forms.py             # Reusable document metadata form components
    │   └── pages/
    │       ├── chatbot.py                # RAG Q&A chat (image upload, history, like/dislike)
    │       ├── queue.py                  # Ingestion queue monitor
    │       ├── documents.py              # Document browser, search & approval
    │       ├── upload.py                 # Document upload with metadata & security tagging
    │       ├── admin.py                  # System administration, site/branch management
    │       ├── users.py                  # User management (roles, departments, clearance)
    │       ├── access.py                 # Access Request Workflow (submit, review, revoke)
    │       ├── audit.py                  # Security & confidential document access log
    │       ├── analytics.py              # Usage analytics
    │       ├── dashboard.py              # Per-department overview dashboard
    │       ├── materials.py              # Bill of Materials (BOM) lookup
    │       ├── lifecycle.py              # Document lifecycle: expiry & review tracking
    │       ├── observability.py          # RAG cost, token, latency observability (Admin)
    │       ├── glossary.py               # Domain synonym/abbreviation dictionary (Admin)
    │       ├── settings.py               # Interface language & personal preferences
    │       ├── feedback.py               # Feedback loop: classify, golden set, regression
    │       └── help.py                   # System user guide
    ├── api/
    │   └── rag_server.py                 # Persistent FastAPI RAG server
    ├── workers/
    │   ├── ingestion_worker.py           # Background document ingestion daemon
    │   └── rag_worker.py                 # Isolated RAG subprocess worker
    ├── ingestion/
    │   ├── document_classifier.py        # 2-tier document classifier
    │   ├── domain_registry.py            # Central domain configuration registry
    │   ├── domain_handlers.py            # Per-domain processing handlers
    │   ├── doc_type_registry.py          # Document type registry
    │   ├── site_registry.py              # Site/branch registry
    │   ├── material_registry.py          # Material registry
    │   ├── mechanical_extractors.py      # Extractor for mechanical documents
    │   ├── generic_extractors.py         # General-purpose extractor
    │   ├── pdf_processor.py              # PDF rendering, Vision OCR, and chunking
    │   ├── sensitive_scanner.py          # Sensitive content detection on ingest
    │   ├── vision_cache.py               # Disk-based Vision OCR result cache
    │   └── file_ingestor.py              # Ingestion pipeline orchestrator
    ├── rag/
    │   ├── service.py                    # Core RAG service wrapper
    │   ├── conversation_state.py         # Conversation state memory and management
    │   ├── interaction_router.py         # RAG semantic routing controller
    │   ├── semantic_cache.py             # Semantic caching layer
    │   ├── route_*.py                    # Various routing logic (LLM, Safety, Responses, Config)
    │   ├── context_builders.py           # Builders for RAG context framing
    │   ├── answer_checks.py              # Verification & evidence checks
    │   ├── glossary_expand.py            # Query expansion via domain glossary
    │   ├── rbac.py                       # RBAC-based Qdrant filter builder
    │   ├── entity_resolver.py            # Entity / material name normalizer
    │   ├── chitchat.py                   # Chitchat detection and bilingual handling
    │   ├── regression.py                 # Regression batch runner
    │   └── text_utils.py                 # Text processing utilities
    ├── auth/
    │   ├── service.py                    # Authentication, session, role & department resolution
    │   ├── security_policy.py            # Security clearance policy (resolve_clearance)
    │   └── rate_limit.py                 # Login rate limiting & lockout
    ├── db/
    │   └── repository.py                 # All SQL Server queries and data operations
    ├── llm/
    │   ├── llm_client.py                 # LLM client (OpenAI-compatible, with retry)
    │   └── vision_client.py              # Vision model client (with retry)
    └── config/
        ├── constants.py                  # System-wide constants (SHARE_ALL_DEPARTMENT sentinel)
        ├── logging.py                    # Centralized structured logging
        ├── settings.py                   # Runtime settings loader
        ├── theme.py                      # Streamlit theme configuration
        └── validate.py                   # Fail-fast config validation (assert_config_valid)
```

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
| `AppSettings` | Key-value application configuration stored in DB |

---

## Getting Started

### 1. Prerequisites

- Python 3.10+ (3.11 matches the Docker image)
- Docker & Docker Compose (recommended)
- Microsoft SQL Server + ODBC Driver (for `pyodbc`)
- A Qdrant Cloud account (URL + API key)
- An OpenAI-compatible LLM endpoint (ProxyLLM or direct OpenAI)

### 2. Configure Environment

Create a `.env` file at the project root:

```env
# LLM / Vision
PROXYLLM_BASE_URL=https://api.proxyllm.eu/v1
PROXYLLM_API_KEY=<your-api-key>
GPT_MODEL_NAME=gpt-5.4
GPT_VISION_MODEL_NAME=gpt-5.4
GPT_TEMPERATURE=0
GPT_MAX_OUTPUT_TOKENS=8000
GPT_VISION_MAX_OUTPUT_TOKENS=16000
GPT_TIMEOUT_SECONDS=300
GPT_VISION_JPEG_QUALITY=95

# Reranking
USE_GPT_RERANK=true
GPT_RERANK_MAX_DOCS=30

# Vector Database
QDRANT_URL=<your-qdrant-cloud-url>
QDRANT_API_KEY=<your-qdrant-api-key>

# Embedding
EMBEDDING_MODEL=BAAI/bge-m3
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
MAX_CONCURRENT_RAG=2
RAG_WORKER_TIMEOUT=240

SEMANTIC_CACHE_ENABLED=false
ENABLE_CONV_STATE=true
ENABLE_HISTORY_SUMMARY=true
ENABLE_CONTEXTUAL_CHUNK=true
SEMANTIC_ROUTER_ENABLED=true
SEMANTIC_ROUTER_SIM_THRESHOLD=0.55
SEMANTIC_ROUTER_MARGIN=0.04

# Strict Modes
STRICT_INGEST_REQUIRE_VISION=true
STRICT_ANSWER_MODE=true

# PDF rendering
PDF_RENDER_DPI=300
METADATA_TEXT_LIMIT=20000
```

For GitHub Actions CI, configure these repository **Secrets**: `QDRANT_URL`, `QDRANT_API_KEY`, `SQL_SERVER`, `SQL_DATABASE`, `SQL_USERNAME`, `SQL_PASSWORD`, `OPENAI_API_KEY`.

> **Config validation:** The app calls `assert_config_valid()` at startup. If any required variable is missing or has the wrong type, it will raise `ConfigError` immediately with a clear list of issues. Secrets are never printed in plain text.

### 3. Database Setup

```bash
# Step 1: Create the base schema
#   Run: database/init/Mech_Chatbot_DB.sql on your SQL Server instance

# Step 2: Apply versioned migrations in order (V0001 → V0013)
python scripts/migrations/migrate.py
# or run each file in database/migrations/ manually

# Step 3: Initialize Qdrant collections
python scripts/create_qdrant_indexes.py
```

> **Security note:** Migrations seed example accounts (`admin`, `reviewer1`, `viewer1`, `uploader1`). Change or remove default credentials before any production deployment.

### 4. Running the Application

**Option A: Docker Compose (recommended)**

```bash
docker-compose -f docker/docker-compose.yml up -d --build
```

This launches:
- Streamlit UI → `http://localhost:8501`
- FastAPI RAG server → `http://localhost:8100`
- Ingestion worker (background)

**Option B: Local Development**

```bash
git clone https://github.com/giabao2605/ChatbotProject.git
cd ChatbotProject
pip install -r requirements.txt
```

Start each service in a separate terminal:

```bash
# Terminal 1 — RAG API server
python run_server.py

# Terminal 2 — Ingestion worker
$env:PYTHONPATH="src"; python run_worker.py

# Terminal 3 — Streamlit UI
streamlit run run.py
```

> Alternatively, run with `PYTHONPATH=src` explicitly:
> ```bash
> PYTHONPATH=src python -m mech_chatbot.api.rag_server
> PYTHONPATH=src python -m mech_chatbot.workers.ingestion_worker
> ```

---

## Application Pages

Access pages via the Streamlit sidebar (visibility depends on your role and clearance):

| Page | Description | Required Role |
|---|---|---|
| **Chatbot Q&A** | Ask technical questions; get evidence-based answers with visual citations, like/dislike feedback, and automatic access request creation when blocked by clearance | All |
| **Ingestion Queue** | Monitor background document processing jobs and status | Uploader, Admin |
| **Documents** | Browse, search, filter, and approve documents | Reviewer, Admin |
| **Upload** | Upload PDF/Word/Excel/images with metadata, security level, and site tagging | Uploader, Admin |
| **Admin** | System settings, site/branch management, app configuration | Admin |
| **Users** | Manage accounts, roles, department access, and security clearance | Admin |
| **Access Requests** | Submit security/department access requests; Reviewers approve/reject; Admins revoke and view history | All (role-filtered tabs) |
| **Audit** | Confidential document access log and permission change history | Admin |
| **Analytics** | System usage statistics and trends | Admin, Reviewer |
| **Dashboard** | Per-department document overview | All |
| **Materials** | Bill of Materials (BOM) lookup and search | All |
| **Lifecycle** | Track document effective dates, expiry, and review deadlines | Reviewer, Admin |
| **Observability** | RAG cost, token, latency, and refusal analytics per department (reads from `RagTraceSummary`) | Admin |
| **Glossary** | Manage domain synonym/abbreviation dictionary; changes take effect immediately | Admin |
| **Settings** | Interface language toggle (Vi/En) and personal preferences | All |
| **Feedback** | Classify disliked answers, manage golden set, run regression tests, document quality ranking, orphan cleanup | Reviewer, Admin |
| **Help** | System user guide | All |

---

## RAG Pipeline (Request Flow)

```
User question
    │
    ▼
Intent Extraction (LLM / Regex fallback)
    │  → Detect part IDs, BOM queries, language, version policy
    ▼
Chitchat Check → respond directly if casual
    │
    ▼
Domain Glossary Expansion (TTL-cached from DB)
    │  → Synonyms & abbreviations added to query
    ▼
HyDE (Hypothetical Document Embedding)
    │  → Activated for short/ambiguous questions
    ▼
Hybrid Search (Dense + BM25) on Qdrant
    │  → RBAC filter: department + security clearance + site + lifecycle status
    ▼
GPT Reranking
    │  → Filter top-N by relevance score
    ▼
Evidence Gate (LLM)
    │  → Verify context is sufficient to answer; else refusal
    ▼
Answer Generation (LLM)
    │  → Response language: Vi / En
    ▼
Answer + Visual Citations + Source Tracking
    │  → Saved to LichSuChat, AnswerSources, RagTraceSummary
    ▼
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

```bash
pip install -r requirements-test.txt

# Full test suite
pytest

# Run by layer
pytest tests/
pytest mech_chatbot_tests_layered/

# Manual RAG quality evaluation
PYTHONPATH=src python scripts/eval/run_ragas_eval.py

# Manual evaluation with detailed output
PYTHONPATH=src python scripts/eval/run_eval.py
```

Evaluation reports are stored in the `reports/` directory.

---

## Useful Scripts

| Script | Purpose |
|---|---|
| `scripts/create_qdrant_indexes.py` | Create or recreate Qdrant collections |
| `scripts/nap_them_file.py` | Manually ingest additional documents |
| `scripts/migrations/migrate.py` | Run pending SQL migrations in order |
| `scripts/eval/run_ragas_eval.py` | Run RAGAS quality evaluation (also used by CI) |
| `scripts/eval/run_eval.py` | Manual evaluation with detailed output |
| `scripts/diagnostics/check_qdrant_count.py` | Verify document count in Qdrant |
| `scripts/diagnostics/check_qdrant_schema.py` | Inspect Qdrant collection schema |
| `scripts/diagnostics/check_image_summary_coverage.py` | Check Vision OCR coverage per document |
| `scripts/ops/backup_system.py` | Back up system state |
| `scripts/admin/hash_pass.py` | Hash passwords for manual account seeding |
| `scripts/danger_ops/empty_bag.py` | Purge documents from Qdrant |
| `scripts/danger_ops/reconcile_sql_qdrant.py` | Reconcile SQL ↔ Qdrant state |
| `scripts/danger_ops/reset_and_create_dev_db.sql` | Full dev database reset |

---

## Contributing

Contributions, bug reports, and feature requests are welcome. Please open an issue on the GitHub repository.

## License

This project is proprietary. All rights reserved.
