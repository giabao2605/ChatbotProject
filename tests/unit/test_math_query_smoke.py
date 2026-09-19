"""Offline smoke evidence checks never send provider requests."""
from datetime import datetime, timezone
import hashlib
import json

import pytest

from scripts.integrated_eval import math_query_dispatch as dispatch


@pytest.mark.parametrize("change,valid", [({}, True), ({"completed_at": "2026-09-07T00:30:00Z"}, False),
    ({"completed_at": "2026-09-06T23:59:59Z"}, False), ({"provider_retries": False}, False),
    ({"successful_requests": "5"}, False), ({"provider_retries": 1}, False),
    ({"provider_configuration_sha256": "b" * 64}, False),
    ({"provider_outcome": {"provider_blocked": True}}, False)])
def test_arm_smoke_is_hash_bound_fresh_exactly_five_successes(change, valid):
    artifact = {"schema": "provider-smoke-v1", "passed": True,
                "request_count": 5, "successful_requests": 5, "failed_requests": 0,
                "provider_retries": 0, "provider_configuration_sha256": "a" * 64,
                "provider_outcome": {"provider_blocked": False},
                "completed_at": "2026-09-07T00:00:00Z", **change}
    raw = json.dumps(artifact).encode()
    kwargs = {"expected_sha256": hashlib.sha256(raw).hexdigest(),
              "expected_provider_sha256": "a" * 64,
              "arm_started_at": datetime(2026, 9, 7, 0, 30, tzinfo=timezone.utc)}
    if valid:
        assert dispatch.validate_matrix_arm_smoke(raw, **kwargs) is None
        with pytest.raises(ValueError, match="matrix_smoke_invalid"):
            dispatch.validate_matrix_arm_smoke(raw + b" ", **kwargs)
    else:
        with pytest.raises(ValueError, match="matrix_smoke_invalid"):
            dispatch.validate_matrix_arm_smoke(raw, **kwargs)
