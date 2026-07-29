"""Persistent JSON-lines BGE-M3 encoder worker for the isolated environment."""

from __future__ import annotations

import json
import sys

from mech_chatbot.rag.late_interaction import encode_query


def main():
    for raw in sys.stdin:
        try:
            request = json.loads(raw)
            vectors = encode_query(str(request.get("query") or ""))
            response = {"id": request.get("id"), "vectors": vectors}
        except Exception as exc:
            response = {"id": None, "error": f"{type(exc).__name__}: {exc}"}
        sys.stdout.write("LATE_RESULT " + json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
