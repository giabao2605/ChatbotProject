from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_BATCH = "crag-eval-v1"
FIXTURE_COLLECTION = "MechChatbot_CRAG_Eval_v1"
FIXTURE_CODE_PREFIX = "CRAG-EVAL-"
FIXTURE_DEPARTMENT = "Technical"
FIXTURE_SITE = "CRAG-EVAL-HQ"
FIXTURE_REMOTE_SITE = "CRAG-EVAL-REMOTE"
DEFAULT_OUTPUT = ROOT / "data" / "crag_eval_v1"
LIVE_OPT_IN = "RUN_CRAG_EVAL_FIXTURE"
