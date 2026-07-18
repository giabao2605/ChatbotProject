"""Hash a local controlled-demo cohort without persisting raw user identity."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path


def build_cohort_hashes(
    user_ids,
    *,
    experiment_id: str,
    assignment_salt: str,
) -> dict:
    experiment = str(experiment_id or "").strip()
    salt = str(assignment_salt or "")
    actors = sorted({str(value).strip() for value in user_ids or [] if str(value).strip()})
    if not experiment or not salt:
        raise ValueError("experiment_id and assignment_salt are required")
    if not 2 <= len(actors) <= 10:
        raise ValueError("controlled demo requires 2-10 unique user IDs")
    actor_hashes = sorted(
        hmac.new(
            salt.encode("utf-8"),
            f"{experiment}|actor|{actor}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        for actor in actors
    )
    cohort_sha256 = hashlib.sha256(
        "\n".join(actor_hashes).encode("utf-8")
    ).hexdigest()
    return {
        "schema": "crag-controlled-demo-cohort-v1",
        "experiment_id": experiment,
        "actor_count": len(actor_hashes),
        "actor_hashes": actor_hashes,
        "cohort_sha256": cohort_sha256,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-ids-file", type=Path, required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    salt = os.getenv("CRAG_PILOT_ASSIGNMENT_SALT", "")
    report = build_cohort_hashes(
        args.user_ids_file.read_text(encoding="utf-8").splitlines(),
        experiment_id=args.experiment_id,
        assignment_salt=salt,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
