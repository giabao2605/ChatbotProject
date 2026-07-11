from types import SimpleNamespace

import pytest
from qdrant_client import models

from mech_chatbot.db.repositories import qdrant


pytestmark = pytest.mark.unit


class _Client:
    def __init__(self, count):
        self._count = count
        self.count_calls = []
        self.set_calls = []
        self.batch_calls = []

    def count(self, **kwargs):
        self.count_calls.append(kwargs)
        return SimpleNamespace(count=self._count)

    def set_payload(self, **kwargs):
        self.set_calls.append(kwargs)

    def batch_update_points(self, **kwargs):
        self.batch_calls.append(kwargs)


def test_filter_based_nested_metadata_write(monkeypatch):
    client = _Client(count=4)
    monkeypatch.setattr(qdrant, "_get_qdrant_client", lambda: client)

    ok = qdrant.update_qdrant_metadata(
        12,
        {"servable": True, "publication_state": "published"},
        require_points=True,
    )

    assert ok is True
    assert len(client.set_calls) == 1
    call = client.set_calls[0]
    assert call["key"] == "metadata"
    assert call["payload"]["servable"] is True
    assert call["wait"] is True
    assert call["ordering"] == models.WriteOrdering.STRONG


def test_missing_points_can_be_required(monkeypatch):
    client = _Client(count=0)
    monkeypatch.setattr(qdrant, "_get_qdrant_client", lambda: client)

    assert qdrant.update_qdrant_metadata(99, {}, require_points=True) is False
    assert qdrant.update_qdrant_metadata(99, {}, require_points=False) is True
    assert client.set_calls == []


def test_batch_metadata_write_uses_one_strongly_ordered_request(monkeypatch):
    client = _Client(count=2)
    monkeypatch.setattr(qdrant, "_get_qdrant_client", lambda: client)

    ok = qdrant.batch_update_qdrant_metadata(
        {
            10: {"servable": False, "is_current": False},
            11: {"servable": True, "is_current": True},
        },
        require_points=True,
    )

    assert ok is True
    assert client.set_calls == []
    assert len(client.batch_calls) == 1
    call = client.batch_calls[0]
    assert call["wait"] is True
    assert call["ordering"] == models.WriteOrdering.STRONG
    assert len(call["update_operations"]) == 2
