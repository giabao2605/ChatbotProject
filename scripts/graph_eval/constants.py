from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
from mech_chatbot.config.constants import EVALUATION_SOURCE_COLLECTIONS

FIXTURE_BATCH = "graph-eval-v1"
FIXTURE_COLLECTION = EVALUATION_SOURCE_COLLECTIONS[FIXTURE_BATCH]
FIXTURE_SITE = "GRAPH-EVAL-HQ"
FIXTURE_REMOTE_SITE = "GRAPH-EVAL-REMOTE"
DEFAULT_OUTPUT = ROOT / "data" / "graph_eval_v1"
LIVE_OPT_IN = "RUN_GRAPH_EVAL_FIXTURE"
