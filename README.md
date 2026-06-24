# Mechanical Engineering RAG Chatbot

An advanced Retrieval-Augmented Generation (RAG) chatbot purpose-built for the mechanical engineering domain. This system processes complex technical PDFs, extracts precise structured data (such as Bills of Materials), and provides highly accurate, evidence-based answers to technical queries without hallucination.

## Key Features

- **Advanced Document Processing:** Automatically parses complex mechanical engineering PDFs, extracting text, tables, and technical drawings.
- **Structured Vision OCR:** Utilizes GPT-5.4 Vision (OpenAI-compatible) to extract and format technical tables and Bills of Materials (BOMs) into a strict, schema-enforced structure (`BangKeVatTu`).
- **Persistent FastAPI RAG Backend:** A decoupled, high-performance API server (`rag_server.py`) that loads models into memory once, ensuring low latency and controlled concurrency for concurrent chat requests.
- **Asynchronous Ingestion Pipeline:** A dedicated background worker (`ingestion_worker.py`) handles heavy document processing, OCR, and embedding generation asynchronously via a managed task queue.
- **Role-Based Access Control (RBAC):** Built-in authentication supporting distinct roles (Admin, Reviewer, Uploader, User) for secure document ingestion, data reviewing, and chat interactions.
- **Anti-Hallucination Guardrails:** Implements a strict "evidence-based" verification layer. The chatbot refuses to guess or hallucinate quantitative data (like fabrication time or costs) if the evidence is missing from the retrieved context.
- **Visual Citations:** Provides explicit reference images in the chat interface so users can verify the source of the information.
- **Multi-App Interface:** Built with **Streamlit**, featuring dedicated portals for conversational chat, task queue management, and administrator approval workflows.
- **Docker Compose Ready:** Supports easy deployment of the entire microservices stack (Frontend, API Backend, Worker).

## Technology Stack

- **Language:** Python 3.9+
- **Frontend / UI:** [Streamlit](https://streamlit.io/)
- **API Backend:** [FastAPI](https://fastapi.tiangolo.com/) & Uvicorn
- **LLM & Vision:** GPT-5.4 / OpenAI-Compatible endpoints (via LangChain)
- **Embeddings:** BAAI/bge-m3 (via sentence-transformers)
- **Vector Database:** [Qdrant Cloud](https://qdrant.tech/)
- **Relational Database:** Microsoft SQL Server
- **Key Libraries:** `PyMuPDF`, `LangChain`, `SQLAlchemy`, `Tenacity`, `Docker`

## Project Structure

```text
ChatBotProject/
├── .env                  # Environment variables (API keys, DB connections)
├── docker-compose.yml    # Container orchestration for UI, API, and Worker
├── app.py                # Main Streamlit UI entry point (Routing & Auth)
├── app_chatbot.py        # User-facing chat interface
├── app_admin.py          # Administrator dashboard for document approval/ingestion
├── app_queue.py          # Task queue & ingestion progress monitoring
├── rag_server.py         # FastAPI application for persistent RAG processing
├── scripts/
│   └── ingestion_worker.py # Background daemon processing pending PDF jobs
├── pdf_processor.py      # Core logic for PDF extraction and Vision OCR
├── rag_logic.py          # Retrieval, generation, and anti-hallucination logic
├── db_logic.py           # SQL Server models, migrations, and operations
├── llm_client.py         # Resilient LangChain wrapper for LLM calls
├── gemini_client.py      # OpenAI-compatible Vision API client (Legacy name)
├── Mech_Chatbot_DB.sql   # SQL Server database initialization script
└── requirements-core.txt # Python dependencies
```

## Getting Started

### 1. Prerequisites

- Python 3.9+ (if running locally without Docker)
- Docker & Docker Compose (Optional, but recommended)
- Microsoft SQL Server
- Qdrant Cloud Account
- OpenAI-compatible API Key (e.g., ProxyLLM or GPT-4/5)

### 2. Database Setup

1. Execute the `Mech_Chatbot_DB.sql` script in your SQL Server instance to create the necessary tables (`BangKeVatTu`, `DocumentPages`, `TechnicalAttributes`, `IngestionJobs`, `LichSuChat`, etc.).
2. Set up a cluster on **Qdrant Cloud** and obtain the URL/API Key.
3. You can initialize Qdrant indexes locally using `python scripts/create_qdrant_indexes.py`.

### 3. Running the Application

**Option A: Using Docker Compose (Recommended)**

```bash
docker-compose up -d --build
```
This will launch:
- The Streamlit UI on `http://localhost:8501`
- The FastAPI RAG Server on `http://localhost:8100`
- The Ingestion Worker in the background.

**Option B: Running Locally (Development)**

1. Install dependencies:
```bash
pip install -r requirements-core.txt
```
2. Start the FastAPI RAG server:
```bash
python rag_server.py
```
3. Start the Background Ingestion worker:
```bash
python scripts/ingestion_worker.py
```
4. Start the Streamlit UI (in a separate terminal):
```bash
streamlit run app.py
```

### 4. Application Modules

Once running, navigate the Streamlit sidebar to access:
- **Chatbot Hỏi Đáp:** Interact with the RAG system and ask mechanical engineering questions.
- **Tiến Trình Ingest:** Monitor background document processing queues and jobs.
- **Duyệt Tài Liệu:** Upload new technical PDFs and commit them to the ingestion queue (Admin/Uploader roles only).

## Contributing

Contributions, issues, and feature requests are welcome. Feel free to check the issues page on the GitHub repository.

## License

This project is proprietary. All rights reserved.
