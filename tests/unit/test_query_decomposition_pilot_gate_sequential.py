"""Offline sequential reconciliation regression tests."""
from datetime import datetime, timedelta, timezone
import pytest
from scripts.ops import query_decomposition_pilot_gate as gate
from tests.unit.test_query_decomposition_pilot_operator import _evidence

VERSION = "query-decomposition-sequential-100-v1"

@pytest.mark.parametrize("mutation,expected", [(None, (True, True)), ("version", (False, True)),
    ("overlap", (True, False)), ("order", (True, False)), ("expired", (False, True))])
def test_sequential_rows_require_bound_version_order_and_deadline(mutation, expected):
    start = datetime(2026, 9, 9, tzinfo=timezone.utc)
    cards = [{"card_id": str(i), "case_id": str(i), "scheduled_at": start.isoformat()} for i in range(100)]
    rows = [{"schema": "query-decomposition-pilot-wal-v1",
             "authorization_sha256": "a" * 64, "schedule_sha256": "b" * 64,
             "attempt_number": 1, "card_id": str(i), "case_id": str(i),
             "attempted_at": (start + timedelta(seconds=2*i)).isoformat(),
             "completed_at": (start + timedelta(seconds=2*i+1)).isoformat(),
             "trace_id_sha256": f"{i:064x}", "runtime_identity_sha256": "c" * 64,
             "evidence": _evidence()} for i in range(100)]
    if mutation == "version": rows[0] = {**rows[0], "pilot_contract_version": gate.PILOT_CONTRACT_VERSION}
    if mutation == "overlap": rows[0] = {**rows[0], "completed_at": rows[1]["completed_at"]}
    if mutation == "order": rows = [rows[1], rows[0], *rows[2:]]
    expires = start + timedelta(seconds=198 if mutation == "expired" else 300)
    assert gate._row_contract_valid(rows, cards, auth_sha="a"*64, schedule_sha="b"*64,
        runtime_identity_sha256="c"*64, expires_at=expires.isoformat(), contract_version=VERSION) == expected
