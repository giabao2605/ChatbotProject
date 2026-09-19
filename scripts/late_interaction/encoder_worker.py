"""Persistent JSON-lines BGE-M3 encoder worker for the isolated environment."""

from __future__ import annotations

import json
import os
import sys

from mech_chatbot.rag import late_interaction


def main():
    encoder = None
    for raw in sys.stdin:
        try:
            request = json.loads(raw)
            query = str(request.get("query") or "")
            if encoder is None:
                config = late_interaction.LateInteractionConfig(
                    query_max_length=int(os.getenv("RAG_LATE_QUERY_MAX_LENGTH", "64")),
                )
                encoder = late_interaction.build_encoder(config)
            vectors = late_interaction.encode_query(
                query, encoder=encoder, max_length=config.query_max_length,
            )
            response = {"id": request.get("id"), "vectors": vectors}
        except Exception as exc:
            response = {"id": None, "error": f"{type(exc).__name__}: {exc}"}
        sys.stdout.write("LATE_RESULT " + json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
