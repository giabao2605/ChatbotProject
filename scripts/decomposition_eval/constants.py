from pathlib import Path

from scripts.crag_eval.constants import FIXTURE_BATCH, FIXTURE_COLLECTION


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "data" / "decomposition_eval_v1"
LIVE_OPT_IN = "RUN_DECOMPOSITION_EVAL_FIXTURE"
BOM_DOCUMENT = "crag_eval_bom_v1.md"
BOM_ROWS = (
    {"row_key": "decomp-row-a", "source_row_id": "table-1-row-1", "part": "CRAG-EVAL-PART-A", "value": "2", "unit": "", "source_table_index": 1, "source_row_index": 1},
    {"row_key": "decomp-row-b", "source_row_id": "table-1-row-2", "part": "CRAG-EVAL-PART-B", "value": "3", "unit": "", "source_table_index": 1, "source_row_index": 2},
)
QUERY_ONLY_TERMINAL_BRANCH_OUTCOMES = {
    "decomp-sql-bom-doc": ("insufficient_evidence", "full_answer"),
    "decomp-bom-alias": ("insufficient_evidence", "full_answer"),
    "decomp-three-source-compare": (
        "full_answer", "insufficient_evidence", "full_answer",
    ),
    "decomp-sufficient-missing": ("full_answer", "insufficient_evidence"),
}
MATH_QUERY_INTERACTION_CASE_IDS = (
    "decomp-sql-bom-doc",
    "decomp-bom-alias",
    "decomp-three-source-compare",
)
