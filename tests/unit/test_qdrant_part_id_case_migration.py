from types import SimpleNamespace

import pytest

from scripts.migrations.backfill_qdrant_part_id_case import backfill_part_id_case


pytestmark = pytest.mark.unit


class FakeQdrantClient:
    def __init__(self):
        self.set_payload_calls = []

    def scroll(self, **_kwargs):
        return [
            SimpleNamespace(
                id=1,
                payload={"metadata": {"ma_chinh": ["Demo-Tech-001"]}},
            ),
            SimpleNamespace(
                id=2,
                payload={"metadata": {"ma_chinh": ["ALREADY-UPPER"]}},
            ),
            SimpleNamespace(id=3, payload={"metadata": None}),
        ], None

    def set_payload(self, **kwargs):
        self.set_payload_calls.append(kwargs)


def test_backfill_part_id_case_updates_only_mixed_case_points():
    client = FakeQdrantClient()

    result = backfill_part_id_case(client, "knowledge")

    assert result == {"scanned": 3, "updated": 1, "failed_point_ids": []}
    assert client.set_payload_calls == [{
        "collection_name": "knowledge",
        "payload": {"ma_chinh": ["DEMO-TECH-001"]},
        "key": "metadata",
        "points": [1],
        "wait": True,
    }]
