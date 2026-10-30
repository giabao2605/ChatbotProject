import sys
import os

# --- FIX crash native 0xC0000005 khi khoi dong worker ---------------------
# Tren Windows, `onnxruntime` (do fastembed/BM25 keo vao) bi ACCESS VIOLATION
# neu duoc nap SAU `torch` (xung dot DLL native onnxruntime x torch).
# `import file_ingestor` -> nap ca stack RAG (torch + sentence_transformers)
# TRUOC onnxruntime -> crash im lang, worker "tu dung".
# Khac phuc: ep `onnxruntime` nap TRUOC MOI import nang khac (truoc torch).
# Phai dat o dau file, truoc khi bat cu module mech_chatbot nao duoc import.
try:
    import onnxruntime  # noqa: F401  # PHAI nap truoc torch de tranh crash DLL
except Exception:
    # Neu moi truong khong co onnxruntime thi bo qua (khong lam vo worker).
    pass

# Keep the root launcher usable from any working directory.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend", "src"))

# Chạy Ingestion Worker
if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    from mech_chatbot.config.validate import assert_config_valid
    assert_config_valid()
    from mech_chatbot.workers.ingestion_worker import run_worker
    run_worker()
