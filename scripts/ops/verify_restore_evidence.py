"""Print the snapshot fingerprint from commit-bound restore-drill evidence."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
for import_root in (ROOT, ROOT / "src"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from mech_chatbot.config.settings import load_settings
from scripts.eval.verify_failure_family_rollback import clean_git_sha
from scripts.ops.restore_drill import verify_restore_evidence


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args(argv)

    settings = load_settings(ROOT / ".env")
    fingerprint = verify_restore_evidence(
        args.evidence,
        expected_sha256=args.sha256,
        current_git_sha=clean_git_sha(ROOT),
        source_database=settings.SQL_DATABASE,
        source_collection=settings.QDRANT_COLLECTION,
        allowed_root=ROOT / ".local" / "restore-drill",
    )
    print(fingerprint)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
