"""Build the immutable technical authorization required before a CRAG pilot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mech_chatbot.evaluation.crag_demo_authorization import (
    build_crag_demo_authorization,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--series", type=Path, required=True)
    parser.add_argument("--provider-smoke", type=Path, action="append", required=True)
    parser.add_argument(
        "--review-mode",
        choices=("multi_reviewer", "single_owner"),
        required=True,
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    artifact = build_crag_demo_authorization(
        series_path=args.series,
        provider_smoke_paths=args.provider_smoke,
        root=args.root,
        review_mode=args.review_mode,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0 if artifact["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
