"""Inspect the configured Qdrant collection schema without mutating it."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mech_chatbot.adapters.qdrant_runtime import build_qdrant_admin_runtime
from mech_chatbot.config.settings import QdrantSettings, load_settings


def main() -> int:
    settings = QdrantSettings.from_settings(load_settings())
    runtime = build_qdrant_admin_runtime(settings)
    try:
        info = runtime.client.get_collection(runtime.collection_name)
        print(f"Schema hien tai cua Qdrant collection '{runtime.collection_name}':")
        print(info.payload_schema)
        return 0
    except Exception as exc:
        print(f"Loi: {exc}")
        return 1
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
