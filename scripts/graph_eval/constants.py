from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_BATCH = "graph-eval-v1"
FIXTURE_COLLECTION = "MechChatbot_Graph_Eval_v1"
FIXTURE_SITE = "GRAPH-EVAL-HQ"
FIXTURE_REMOTE_SITE = "GRAPH-EVAL-REMOTE"
DEFAULT_OUTPUT = ROOT / "data" / "graph_eval_v1"
LIVE_OPT_IN = "RUN_GRAPH_EVAL_FIXTURE"
