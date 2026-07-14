"""Read-only preflight for isolated CRAG pilot deployments."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mech_chatbot.evaluation.crag_pilot import validate_deployment_contract


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    deployment_urls = config.get("deployment_urls") or {}
    if not deployment_urls.get("control") or not deployment_urls.get("candidate"):
        raise ValueError("config requires control and candidate deployment_urls")

    def health(arm):
        response = requests.get(
            f"{str(deployment_urls[arm]).rstrip('/')}/health",
            timeout=args.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    report = validate_deployment_contract(
        config, health("control"), health("candidate")
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
