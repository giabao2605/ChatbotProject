import os

import pytest

from scripts.decomposition_eval.constants import LIVE_OPT_IN
from scripts.decomposition_eval.generate_manifest import cases
from scripts.decomposition_eval.preflight import run_live_preflight


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv(LIVE_OPT_IN) != "1" or os.getenv("QDRANT_COLLECTION") != "MechChatbot_CRAG_Eval_v1",
        reason="explicit decomposition fixture and staging Qdrant opt-ins are required",
    ),
]


def test_decomposition_fixture_is_published_current_and_governed():
    manifest_cases = cases()
    report = run_live_preflight(manifest_cases)

    assert report["passed"] is True, report["failures"]
    assert report["checked_cases"] == len(manifest_cases)
    assert report["case_resolutions"]["decomp-access-denied"]["expected_branches"][1]["expected_citations"] == []
