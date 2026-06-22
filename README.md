# Mechanical Engineering RAG Chatbot

An advanced Retrieval-Augmented Generation (RAG) chatbot purpose-built for the mechanical engineering domain. This system processes complex technical PDFs, extracts precise structured data (such as Bills of Materials), and provides highly accurate, evidence-based answers to technical queries without hallucination.

## Key Features

- **Advanced Document Processing:** Automatically parses complex mechanical engineering PDFs, extracting text, tables, and technical drawings.
- **Structured Vision OCR:** Utilizes the Google Gemini Vision API to extract and format technical tables and Bills of Materials (BOMs) into a strict, schema-enforced structure (`BangKeVatTu`).
- **Cloud-Native Vector Search:** Integrated with **Qdrant Cloud** for high-performance, scalable semantic search.
- **Robust Relational Metadata:** Uses **SQL Server** to maintain rich metadata, document relationships, and persistent chat history with image citations.
- **Anti-Hallucination Guardrails:** Implements a strict "evidence-based" verification layer. The chatbot refuses to guess or hallucinate quantitative data (like fabrication time or costs) if the evidence is missing from the retrieved context.
- **Visual Citations:** Provides explicit reference images in the chat interface so users can verify the source of the information.
- **Multi-App Interface:** Built with **Streamlit**, featuring dedicated portals for users and administrators.

## Technology Stack

- **Language:** Python 3
- **Frontend / UI:** [Streamlit](https://streamlit.io/)
- **LLM & Vision:** [Google Gemini API](https://deepmind.google/technologies/gemini/)
- **Vector Database:** [Qdrant Cloud](https://qdrant.tech/)
- **Relational Database:** Microsoft SQL Server
- **Key Libraries:** `PyMuPDF` (PDF processing), `qdrant-client`, `pyodbc` (SQL connection)

## Project Structure

```text
ChatBotProject/
├── .env                  # Environment variables (API keys, DB connections)
├── app.py                # Main entry point for the Streamlit app
├── app_chatbot.py        # User-facing chat interface
├── app_admin.py          # Administrator dashboard for document ingestion
├── app_queue.py          # Task queue management interface
├── pdf_processor.py      # Core logic for PDF extraction and Gemini Vision OCR
├── rag_logic.py          # Retrieval, generation, and anti-hallucination logic
├── db_logic.py           # SQL Server operations and schema management
├── gemini_client.py      # Wrapper for Gemini API interactions
├── Mech_Chatbot_DB.sql   # SQL Server database initialization script
└── requirements.txt      # Python dependencies
```

## Getting Started

### 1. Prerequisites

- Python 3.9+
- Microsoft SQL Server
- Qdrant Cloud Account
- Google Gemini API Key

### 2. Installation

Clone the repository and install the required dependencies:

```bash
git clone https://github.com/your-username/mechanical-rag-chatbot.git
cd mechanical-rag-chatbot
pip install -r requirements.txt
```

### 3. Database Setup

1. Execute the `Mech_Chatbot_DB.sql` script in your SQL Server instance to create the necessary tables (`BangKeVatTu`, `RefImages`, chat histories, etc.).
2. Set up a cluster on **Qdrant Cloud**.

### 4. Environment Configuration

Create a `.env` file in the root directory (based on the provided `.env.example` if applicable) and configure your credentials:

```env
SQL_SERVER=localhost\SQLEXPRESS
SQL_DATABASE=Mech_Chatbot_DB
SQL_DRIVER=ODBC Driver 17 for SQL Server

QDRANT_URL=...
QDRANT_API_KEY=...

COHERE_API_KEY=...
COHERE_MODEL_NAME=command-r-08-2024

GOOGLE_API_KEY=...
GEMINI_VISION_MODEL=gemini-2.5-flash

EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_DIM=1024
```

### 5. Running the Application

Launch the Streamlit application:

```bash
streamlit run app.py
```

From the main menu, you can navigate to:
- **Admin Portal (`app_admin.py`):** Upload new PDFs, process them, and index them into Qdrant/SQL.
- **Chatbot Portal (`app_chatbot.py`):** Interact with the RAG system and ask mechanical engineering questions.

## Contributing

Contributions, issues, and feature requests are welcome. Feel free to check the issues page on the GitHub repository.

## License

This project is proprietary. All rights reserved.
