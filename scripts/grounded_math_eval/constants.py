from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
from mech_chatbot.config.constants import EVALUATION_SOURCE_COLLECTIONS

FIXTURE_BATCH = "grounded-math-eval-v1"
FIXTURE_COLLECTION = EVALUATION_SOURCE_COLLECTIONS[FIXTURE_BATCH]
FIXTURE_CODE_PREFIX = "GROUND-MATH-EVAL-"
FIXTURE_DEPARTMENT = "Technical"
FIXTURE_SITE = "GROUND-MATH-EVAL-HQ"
DEFAULT_OUTPUT = ROOT / "data" / "grounded_math_eval_v1"
LIVE_OPT_IN = "RUN_GROUNDED_MATH_EVAL_FIXTURE"
