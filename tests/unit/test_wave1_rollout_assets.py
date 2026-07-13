from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = ROOT / "scripts" / "eval" / "templates" / "wave1"


def test_wave1_manifest_templates_are_intentionally_empty():
    expected = {"technical.jsonl", "hr.jsonl", "purchasing.jsonl"}
    assert {path.name for path in TEMPLATE_DIR.glob("*.jsonl")} == expected
    for path in TEMPLATE_DIR.glob("*.jsonl"):
        records = [
            line for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        assert records == []


def test_wave1_assets_document_real_rollout_requirements():
    guide = (TEMPLATE_DIR / "README.md").read_text(encoding="utf-8")
    checklist = (ROOT / "docs" / "wave1-rollout-checklist.md").read_text(encoding="utf-8")
    for marker in ("Technical", "HR", "Purchasing", "--minimum 75", "pilot-eval-v4"):
        assert marker in guide
    for marker in ("taxonomy", "RBAC", "Dark launch", "missing_prerequisites"):
        assert marker.lower() in checklist.lower()
