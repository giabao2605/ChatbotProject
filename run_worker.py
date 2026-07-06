import sys
import os

# Thêm src/ vào Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

# Chạy Ingestion Worker
if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    from dotenv import load_dotenv
    load_dotenv()
    from mech_chatbot.config.validate import assert_config_valid
    assert_config_valid()
    from mech_chatbot.workers.ingestion_worker import run_worker
    run_worker()
