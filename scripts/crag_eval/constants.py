from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
from mech_chatbot.config.constants import EVALUATION_SOURCE_COLLECTIONS

FIXTURE_BATCH = "crag-eval-v1"
FIXTURE_COLLECTION = EVALUATION_SOURCE_COLLECTIONS[FIXTURE_BATCH]
FIXTURE_CODE_PREFIX = "CRAG-EVAL-"
FIXTURE_DEPARTMENT = "Technical"
FIXTURE_SITE = "CRAG-EVAL-HQ"
FIXTURE_REMOTE_SITE = "CRAG-EVAL-REMOTE"
DEFAULT_OUTPUT = ROOT / "data" / "crag_eval_v1"
LIVE_OPT_IN = "RUN_CRAG_EVAL_FIXTURE"
