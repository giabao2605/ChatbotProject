from types import SimpleNamespace

import pytest

from scripts.late_interaction.backfill_shadow import (
    backfill,
    build_readiness_artifact,
    estimate_storage,
    write_readiness_artifacts,
)


pytestmark = pytest.mark.unit


def source_point(point_id="source-1", **metadata_overrides):
    metadata = {
        "doc_id": 41,
        "trang_so": 3,
        "chunk_index": None,
        "version_no": 12,
        "owner_department": "Technical",
        "phong_ban_quyen": ["Technical"],
        "site": "HQ",
        "security_level": "internal",
        "publication_state": "published",
        "lifecycle_status": "published",
        "review_status": "approved",
        "is_current": True,
        "servable": True,
        "noi_dung_goc": "BOM PART-A quantity 2",
    }
    metadata.update(metadata_overrides)
    return SimpleNamespace(
        id=point_id,
        payload={"page_content": "tokenized", "metadata": metadata},
    )


class MemoryQdrant:
    def __init__(self, source_points):
        self.source_points = list(source_points)
        self.shadow = {}
        self.created = False

    def collection_exists(self, name):
        return self.created if name == "shadow" else True

    def create_collection(self, **_kwargs):
        self.created = True

    def create_payload_index(self, **_kwargs):
        pass

    def get_collection(self, _name):
        late = SimpleNamespace(
            size=1024,
            datatype="float16",
            hnsw_config=SimpleNamespace(m=0),
            multivector_config=SimpleNamespace(comparator="max_sim"),
        )
        return SimpleNamespace(
            config=SimpleNamespace(params=SimpleNamespace(vectors={"late": late})),
            payload_schema={
                "candidate_key": SimpleNamespace(data_type="keyword"),
                "index_version": SimpleNamespace(data_type="keyword"),
            },
        )

    def scroll(self, *, collection_name, scroll_filter=None, **_kwargs):
        if collection_name == "source":
            return self.source_points, None
        points = list(self.shadow.values())
        if scroll_filter is not None:
            wanted = set(scroll_filter.must[0].match.any)
            points = [point for point in points if point.payload.get("candidate_key") in wanted]
        return points, None

    def upsert(self, *, points, **_kwargs):
        if not points:
            raise AssertionError("backfill must not send an empty upsert")
        for point in points:
            self.shadow[point.id] = SimpleNamespace(
                id=point.id, payload=point.payload, vector=point.vector,
            )

    def set_payload(self, *, payload, points, **_kwargs):
        for point_id in points:
            self.shadow[point_id].payload.update(payload)

    def delete(self, *, points_selector, **_kwargs):
        for point_id in points_selector.points:
            self.shadow.pop(point_id, None)


def test_backfill_supports_missing_chunk_index_and_is_idempotent():
    client = MemoryQdrant([source_point()])
    encoded_batches = []

    def encoder(texts):
        encoded_batches.append(tuple(texts))
        return [[[0.1] * 1024] for _ in texts]

    first = backfill(
        client, "source", "shadow", index_version="late-v1", encoder=encoder,
    )
    second = backfill(
        client, "source", "shadow", index_version="late-v1", encoder=encoder,
    )

    payload = next(iter(client.shadow.values())).payload
    assert payload["canonical_chunk_index"] == ""
    assert payload["source_point_id"] == "source-1"
    assert first["uploaded"] == 1
    assert first["eligible"] == 1
    assert first["coverage"] == 1.0
    assert second["already_valid"] == 1
    assert second["uploaded"] == 0
    assert len(encoded_batches) == 1
    assert encoded_batches[0] == ("BOM PART-A quantity 2",)


def test_backfill_repairs_governance_without_reencoding():
    client = MemoryQdrant([source_point(site="HQ")])
    encoder_calls = []
    encoder = lambda texts: encoder_calls.append(tuple(texts)) or [[[0.1] * 1024] for _ in texts]
    backfill(client, "source", "shadow", encoder=encoder)
    client.source_points = [source_point(site="PLANT-2")]

    report = backfill(client, "source", "shadow", encoder=encoder)

    payload = next(iter(client.shadow.values())).payload
    assert report["governance_repaired"] == 1
    assert report["provenance_drift"] == 0
    assert report["coverage"] == 1.0
    assert payload["site"] == "PLANT-2"
    assert len(encoder_calls) == 1


def test_backfill_reports_missing_governance_fields_and_nonservable_state():
    client = MemoryQdrant([
        source_point("missing-site", site=None),
        source_point("not-servable", servable=False, noi_dung_goc="different"),
    ])

    report = backfill(
        client, "source", "shadow", encoder=lambda texts: [[[0.1] * 1024] for _ in texts],
    )

    assert report["source_total"] == 2
    assert report["eligible"] == 1
    assert report["governance_rejected"] == 1
    assert report["governance_missing_fields"] == {"site": 1}
    assert report["non_servable_sources"] == 1
    assert report["coverage"] == 1.0
    assert len(client.shadow) == 1
    assert next(iter(client.shadow.values())).payload["servable"] is False


def test_backfill_does_not_count_payload_only_existing_point_as_covered():
    client = MemoryQdrant([source_point()])
    encoder = lambda texts: [[[0.1] * 1024] for _ in texts]
    backfill(client, "source", "shadow", encoder=encoder)
    existing = next(iter(client.shadow.values()))
    existing.vector = None

    report = backfill(client, "source", "shadow", encoder=encoder)

    assert report["vector_schema_rejected"] == 1
    assert report["coverage"] == 0.0


def test_duplicate_source_points_share_one_shadow_candidate_and_full_coverage():
    client = MemoryQdrant([source_point("source-1"), source_point("source-2")])

    report = backfill(
        client, "source", "shadow", encoder=lambda texts: [[[0.1] * 1024] for _ in texts],
    )

    payload = next(iter(client.shadow.values())).payload
    assert report["source_total"] == 2
    assert report["unique_candidates"] == 1
    assert report["duplicate_sources"] == 1
    assert report["uploaded"] == 1
    assert report["coverage"] == 1.0
    assert payload["source_point_ids"] == ["source-1", "source-2"]


def test_duplicate_candidate_with_conflicting_governance_fails_closed_across_batches():
    client = MemoryQdrant([
        source_point("source-1", site="HQ"),
        source_point("source-2", site="PLANT-2"),
    ])

    report = backfill(
        client,
        "source",
        "shadow",
        batch_size=1,
        encoder=lambda texts: [[[0.1] * 1024] for _ in texts],
    )

    assert report["provenance_drift"] == 1
    assert report["coverage"] == 0.0


def test_new_index_version_reencodes_existing_candidate_instead_of_drift():
    client = MemoryQdrant([source_point()])
    calls = []
    encoder = lambda texts: calls.append(tuple(texts)) or [[[0.1] * 1024] for _ in texts]
    backfill(client, "source", "shadow", index_version="late-v1", encoder=encoder)

    report = backfill(
        client, "source", "shadow", index_version="late-v2", encoder=encoder,
    )

    payload = next(iter(client.shadow.values())).payload
    assert report["stale_reindexed"] == 1
    assert report["provenance_drift"] == 0
    assert report["coverage"] == 1.0
    assert payload["index_version"] == "late-v2"
    assert len(calls) == 2


def test_backfill_blocks_provenance_drift_and_prunes_only_when_opted_in():
    client = MemoryQdrant([source_point()])
    encoder = lambda texts: [[[0.1] * 1024] for _ in texts]
    backfill(client, "source", "shadow", encoder=encoder)
    current = next(iter(client.shadow.values()))
    current.payload["version_no"] = 99
    client.shadow["orphan-id"] = SimpleNamespace(
        id="orphan-id", payload={"candidate_key": "orphan-key"},
    )

    blocked = backfill(client, "source", "shadow", encoder=encoder)

    assert blocked["provenance_drift"] == 1
    assert blocked["coverage"] == 0.0
    assert blocked["orphan_points"] == 1
    assert "orphan-id" in client.shadow

    pruned = backfill(
        client, "source", "shadow", encoder=encoder, prune_orphans=True,
    )

    assert pruned["pruned_orphans"] == 1
    assert pruned["orphan_points"] == 0
    assert "orphan-id" not in client.shadow


def test_storage_estimate_uses_source_points_not_indexed_vector_count():
    storage = estimate_storage(
        source_points=170,
        source_dimension=1024,
        shadow_token_vectors=1000,
        shadow_dimension=1024,
    )

    assert storage["source_dense_bytes"] == 170 * 1024 * 4
    assert storage["shadow_vector_bytes"] == 1000 * 1024 * 2
    assert storage["shadow_storage_ratio"] == pytest.approx(1000 * 2 / (170 * 4))


def test_readiness_artifact_is_flattened_for_gate_and_redacts_configuration(tmp_path):
    artifact = build_readiness_artifact(
        qdrant_report={
            "server_version": "1.18.2",
            "client_version": "1.15.1",
            "source_collection": "source",
            "points_count": 170,
            "shadow_points_count": 170,
            "capability_passed": True,
            "ready_for_serving": True,
            "shadow_schema": {"passed": True},
        },
        encoder_report={"passed": True, "query_shape": [8, 1024]},
        backfill_report={
            "coverage": 1.0,
            "governance_rejected": 0,
            "provenance_drift": 0,
            "vector_schema_rejected": 0,
            "orphan_points": 0,
        },
        storage_report={"shadow_storage_ratio": 12.5},
        benchmark_report={"encode_p95_ms": 10, "query_p95_ms": 20},
        commit_sha="abc123",
        started_at="2026-07-14T00:00:00Z",
        ended_at="2026-07-14T00:01:00Z",
        configuration={
            "source_collection": "source",
            "shadow_collection": "shadow",
            "api_key": "must-not-leak",
        },
    )

    json_path, markdown_path = write_readiness_artifacts(tmp_path, artifact)

    assert artifact["schema"] == "late-interaction-readiness-v1"
    assert artifact["shadow_coverage"] == 1.0
    assert artifact["governance_drift"] == 0
    assert artifact["ready_for_serving"] is True
    assert "api_key" not in artifact["configuration"]
    assert "must-not-leak" not in json_path.read_text(encoding="utf-8")
    markdown = markdown_path.read_text(encoding="utf-8")
    assert markdown.startswith("# Late Interaction Readiness")
    assert '"client_version": "1.15.1"' in markdown
    assert "## Encoder" in markdown
    assert "## Backfill" in markdown
