from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_BATCH = "grounded-math-eval-v1"
FIXTURE_COLLECTION = "MechChatbot_GroundedMath_Eval_v1"
FIXTURE_CODE_PREFIX = "GROUND-MATH-EVAL-"
FIXTURE_DEPARTMENT = "Technical"
FIXTURE_SITE = "GROUND-MATH-EVAL-HQ"
DEFAULT_OUTPUT = ROOT / "data" / "grounded_math_eval_v1"
LIVE_OPT_IN = "RUN_GROUNDED_MATH_EVAL_FIXTURE"
