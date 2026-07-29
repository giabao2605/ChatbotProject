from pathlib import Path

from scripts.crag_eval.constants import FIXTURE_BATCH, FIXTURE_COLLECTION


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "data" / "decomposition_eval_v1"
LIVE_OPT_IN = "RUN_DECOMPOSITION_EVAL_FIXTURE"
BOM_DOCUMENT = "crag_eval_bom_v1.md"
BOM_ROWS = (
    {"row_key": "decomp-row-a", "part": "CRAG-EVAL-PART-A", "value": "2", "unit": "cái", "source_table_index": 1},
    {"row_key": "decomp-row-b", "part": "CRAG-EVAL-PART-B", "value": "3", "unit": "cái", "source_table_index": 2},
)
