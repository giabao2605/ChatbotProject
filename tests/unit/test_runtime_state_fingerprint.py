from types import SimpleNamespace

import pytest

from scripts.ops.capture_runtime_state import build_runtime_state_identity


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self.rows


class _Connection:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, statement):
        table = str(statement).split("[", 1)[1].split("]", 1)[0]
        return _Rows(self.rows.get(table, ()))


class _Qdrant:
    def __init__(self, points, *, reported_count=None):
        self.points = list(points)
        self.reported_count = (
            len(self.points) if reported_count is None else reported_count
        )

    def scroll(self, **kwargs):
        assert kwargs["with_payload"] is True
        assert kwargs["with_vectors"] is True
        return self.points, None

    def count(self, **kwargs):
        assert kwargs["exact"] is True
        return SimpleNamespace(count=self.reported_count)

    def get_collection(self, _collection):
        config = SimpleNamespace(
            model_dump=lambda **_kwargs: {
                "params": {"vectors": {"size": 1, "distance": "Cosine"}}
            }
        )
        return SimpleNamespace(config=config, payload_schema={})


def _point(point_id, vector):
    return SimpleNamespace(
        id=point_id,
        payload={"metadata": {"doc_id": point_id, "serving_epoch": 7}},
        vector=vector,
    )


def test_runtime_state_fingerprint_is_order_independent_and_content_bound():
    rows = {
        "TaiLieu": [
            {"DocID": 2, "ServingEpoch": 8},
            {"DocID": 1, "ServingEpoch": 7},
        ],
    }
    values = {
        "git_sha": "a" * 40,
        "database": "Mech_Chatbot_DB",
        "collection": "TaiLieuKyThuat_v2",
    }
    first = build_runtime_state_identity(
        _Connection(rows),
        _Qdrant([_point(2, [0.2]), _point(1, [0.1])]),
        **values,
    )
    reordered = build_runtime_state_identity(
        _Connection({"TaiLieu": list(reversed(rows["TaiLieu"]))}),
        _Qdrant([_point(1, [0.1]), _point(2, [0.2])]),
        **values,
    )
    changed = build_runtime_state_identity(
        _Connection(rows),
        _Qdrant([_point(2, [9.9]), _point(1, [0.1])]),
        **values,
    )

    assert first["snapshot_fingerprint"] == reordered["snapshot_fingerprint"]
    assert first["snapshot_fingerprint"] != changed["snapshot_fingerprint"]
    assert first["qdrant"]["point_count"] == 2
    assert len(first["qdrant"]["config_sha256"]) == 64


def test_runtime_state_fingerprint_rejects_incomplete_qdrant_scan():
    with pytest.raises(RuntimeError, match="point count"):
        build_runtime_state_identity(
            _Connection({}),
            _Qdrant([_point(1, [0.1])], reported_count=2),
            git_sha="a" * 40,
            database="Mech_Chatbot_DB",
            collection="TaiLieuKyThuat_v2",
        )


def test_runtime_state_fingerprint_rejects_same_count_qdrant_drift():
    class DriftingQdrant(_Qdrant):
        def __init__(self):
            super().__init__([_point(1, [0.1])])
            self.scroll_calls = 0

        def scroll(self, **kwargs):
            self.scroll_calls += 1
            if self.scroll_calls == 2:
                self.points = [_point(1, [9.9])]
            return super().scroll(**kwargs)

    with pytest.raises(RuntimeError, match="changed during state capture"):
        build_runtime_state_identity(
            _Connection({}),
            DriftingQdrant(),
            git_sha="a" * 40,
            database="Mech_Chatbot_DB",
            collection="TaiLieuKyThuat_v2",
        )
