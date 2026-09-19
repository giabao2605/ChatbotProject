"""Split the controlled-demo manifest into immutable feature evaluation scopes."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from scripts.controlled_demo_eval.milestones import EXPECTED_GROUP_COUNTS, MILESTONES


GROUPS = (
    "factual",
    "insufficient_evidence",
    "access_denied",
    "grounded_math",
    "complex",
    "graphrag",
    "global",
)

def _write_jsonl(path: Path, rows: list[dict]) -> str:
    payload = "".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
        for row in rows
    )
    path.write_text(payload, encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_manifests(
    cases: list[dict],
    output_dir: Path,
    *,
    source_manifests=(),
    expected_group_counts=None,
) -> dict:
    """Write stable per-group and per-milestone manifests without changing cases."""
    output_dir = Path(output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty output directory: {output_dir}")

    seen = set()
    grouped = {group: [] for group in GROUPS}
    for case in cases:
        case_id = str(case.get("id") or "").strip()
        if not case_id:
            raise ValueError("every controlled-demo case requires an id")
        if case_id in seen:
            raise ValueError(f"duplicate case id: {case_id}")
        seen.add(case_id)
        group = str(case.get("evaluation_group") or "").strip()
        if group not in grouped:
            raise ValueError(f"unsupported evaluation_group: {group or '<empty>'}")
        grouped[group].append(case)

    if expected_group_counts is not None:
        actual_counts = {group: len(rows) for group, rows in grouped.items()}
        expected_counts = {
            group: int(expected_group_counts.get(group, 0)) for group in GROUPS
        }
        if actual_counts != expected_counts:
            raise ValueError(
                f"controlled-demo group distribution mismatch: "
                f"expected {expected_counts}, got {actual_counts}"
            )

    source_references = []
    for source in source_manifests:
        source = Path(source)
        if not source.is_file():
            raise ValueError(f"source manifest does not exist: {source}")
        source_references.append({
            "path": str(source.resolve()),
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        })

    output_dir.mkdir(parents=True, exist_ok=True)
    group_reports = {}
    for group, rows in grouped.items():
        path = output_dir / f"{group}.jsonl"
        sha256 = _write_jsonl(path, rows)
        group_reports[group] = {
            "path": str(path.resolve()),
            "case_count": len(rows),
            "sha256": sha256,
        }

    milestone_reports = {}
    for milestone, config in MILESTONES.items():
        scope_groups = config.groups
        minimum = config.minimum_cases
        rows = [
            case
            for case in cases
            if case.get("evaluation_group") in scope_groups
        ]
        path = output_dir / f"{milestone}.jsonl"
        sha256 = _write_jsonl(path, rows)
        milestone_reports[milestone] = {
            "path": str(path.resolve()),
            "groups": list(scope_groups),
            "case_count": len(rows),
            "minimum_cases": minimum,
            "minimum_met": len(rows) >= minimum,
            "sha256": sha256,
        }

    report = {
        "schema": "controlled-demo-manifest-inventory-v1",
        "source_case_count": len(cases),
        "source_manifests": source_references,
        "groups": group_reports,
        "milestones": milestone_reports,
    }
    (output_dir / "inventory.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    from scripts.eval.run_eval import load_manifest_files

    report = prepare_manifests(
        load_manifest_files(args.manifest),
        args.output_dir,
        source_manifests=args.manifest,
        expected_group_counts=EXPECTED_GROUP_COUNTS,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
