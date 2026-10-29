"""Characterization tests for publication workflow and outbox public seams.

The SQL integration suite remains authoritative for real transaction semantics.
These tests isolate external boundaries so workflow branches fail fast in the
default unit suite.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.exc import IntegrityError

from mech_chatbot.db.repositories import publication


pytestmark = pytest.mark.unit


class _Result:
    def __init__(self, *, mapped=None, row=None, rows=None):
        self._mapped = mapped
        self._row = row
        self._rows = list(rows or [])

    def mappings(self):
        return self

    def first(self):
        return self._mapped

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)

    def all(self):
        return list(self._rows)


class _Connection:
    def __init__(self, engine):
        self._engine = engine

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        normalized_params = dict(params or {})
        self._engine.calls.append((sql, normalized_params))
        return self._engine.dispatch(sql, normalized_params)


class _Engine:
    def __init__(self, dispatch):
        self.dispatch = dispatch
        self.calls = []

    def connect(self):
        return _Connection(self)

    def begin(self):
        return _Connection(self)


def _valid(doc_id=42):
    return publication.ValidationResult(doc_id=doc_id, valid=True)


def _install_engine(monkeypatch, dispatch):
    fake_engine = _Engine(dispatch)
    monkeypatch.setattr(publication, "engine", fake_engine)
    monkeypatch.setattr(publication, "_ensure_engine", lambda: None)
    return fake_engine


def _install_validations(monkeypatch):
    monkeypatch.setattr(
        publication,
        "validate_publish_contract",
        lambda doc_id: _valid(int(doc_id)),
    )
    monkeypatch.setattr(
        publication,
        "validate_publish_actor",
        lambda doc_id, *_args, **_kwargs: _valid(int(doc_id)),
    )


def test_publish_document_rejects_action_and_doc_id_before_touching_boundaries(
    monkeypatch,
):
    monkeypatch.setattr(
        publication,
        "_create_outbox_event",
        lambda *_args, **_kwargs: pytest.fail("outbox must not be touched"),
    )

    invalid_action = publication.publish_document(42, action="delete")
    invalid_doc = publication.publish_document("not-an-id")

    assert invalid_action.to_dict() == {
        "ok": False,
        "doc_id": 42,
        "outbox_id": None,
        "state": "validation_failed",
        "error": "Publish action 'delete' khong hop le",
        "validation": None,
    }
    assert invalid_doc.doc_id is None
    assert invalid_doc.state == "validation_failed"
    assert invalid_doc.error == "DocID khong hop le"


@pytest.mark.parametrize(
    ("outbox_status", "expected_ok", "expected_state"),
    [("processing", True, "processing"), ("done", True, "published"), ("failed", False, "failed")],
)
def test_publish_reports_actual_outbox_state_when_own_claim_loses(
    monkeypatch, outbox_status, expected_ok, expected_state,
):
    _install_engine(
        monkeypatch,
        lambda sql, _params: _Result(row=(outbox_status,))
        if "SELECT Status FROM dbo.PublicationOutbox" in sql else _Result(),
    )
    monkeypatch.setattr(
        publication, "_create_outbox_event",
        lambda *_args, **_kwargs: publication.PublicationResult(
            ok=True, doc_id=1149, outbox_id=10345, state="publishing"
        ),
    )
    monkeypatch.setattr(publication, "process_publication_outbox_once", lambda **_kwargs: None)

    result = publication.publish_document(
        1149, action="standalone", reviewer="reviewer_b", reviewer_id=100,
        reviewer_roles=["reviewer"],
    )

    assert result.ok is expected_ok
    assert result.doc_id == 1149
    assert result.outbox_id == 10345
    assert result.state == expected_state
    if outbox_status == "failed":
        assert result.error


@pytest.mark.parametrize(
    ("variant_code", "collision"),
    [("default", None), ("A", {"DocID": 168})],
)
def test_new_variant_with_conflicting_code_is_rejected_before_outbox_write(
    monkeypatch, variant_code, collision,
):
    _install_validations(monkeypatch)

    def dispatch(sql, _params):
        if "SELECT PublicationState, Servable, PublicationVersion" in sql:
            return _Result(mapped={
                "PublicationState": "draft", "Servable": False, "PublicationVersion": 0,
            })
        if "SELECT BaseCode, VariantCode" in sql:
            return _Result(mapped={"BaseCode": "QA-PUMP", "VariantCode": variant_code})
        if "SELECT TOP 1 DocID FROM dbo.TaiLieu" in sql:
            return _Result(mapped=collision)
        if "INSERT INTO dbo.PublicationOutbox" in sql or "SET PublicationState = 'publishing'" in sql:
            pytest.fail("invalid variant must not create outbox or change document")
        return _Result()

    engine = _install_engine(monkeypatch, dispatch)
    monkeypatch.setattr(
        publication, "process_publication_outbox_once",
        lambda **_kwargs: pytest.fail("invalid variant must not process"),
    )

    result = publication.publish_document(
        170, action="new_variant", reviewer="reviewer_b", reviewer_id=100,
        reviewer_roles=["reviewer"],
    )

    assert result.state == "validation_failed"
    assert result.ok is False
    assert "biến thể" in result.error.lower()
    assert not any("INSERT INTO dbo.PublicationOutbox" in sql for sql, _ in engine.calls)


def test_corrected_variant_can_create_new_event_after_old_event_exhausted(monkeypatch):
    _install_validations(monkeypatch)

    def dispatch(sql, _params):
        if "SELECT PublicationState, Servable, PublicationVersion" in sql:
            return _Result(mapped={"PublicationState": "failed", "Servable": False, "PublicationVersion": 1})
        if "SELECT BaseCode, VariantCode" in sql:
            return _Result(mapped={"BaseCode": "QA-PUMP", "VariantCode": "beta"})
        if "SELECT TOP 1 OutboxID FROM dbo.PublicationOutbox" in sql:
            return _Result(mapped={"OutboxID": 353})
        if "SELECT TOP 1 OutboxID, Status" in sql:
            return _Result(mapped=None)
        if "INSERT INTO dbo.PublicationOutbox" in sql:
            return _Result(row=(354,))
        return _Result()

    engine = _install_engine(monkeypatch, dispatch)
    result = publication._create_outbox_event(
        170, "new_variant", "reviewer_b", reviewer_id=100, reviewer_roles=["reviewer"]
    )

    assert result.ok is True
    assert result.outbox_id == 354
    inserts = [params for sql, params in engine.calls if "INSERT INTO dbo.PublicationOutbox" in sql]
    assert inserts == [{
        "doc_id": 170, "action": "new_variant",
        "payload": inserts[0]["payload"], "key": "publication:170:new_variant:2:after:353",
    }]


def test_queued_new_variant_is_revalidated_before_qdrant_changes(monkeypatch):
    _install_validations(monkeypatch)

    def dispatch(sql, _params):
        if "SELECT BaseCode, VariantCode" in sql:
            return _Result(mapped={"BaseCode": "QA-PUMP", "VariantCode": "default"})
        if "sp_getapplock" in sql:
            return _Result(row=(0,))
        if "SELECT DocID, BaseCode, VariantCode" in sql:
            return _Result(mapped={
                "DocID": 170, "BaseCode": "QA-PUMP", "VariantCode": "default",
                "PublicationVersion": 0, "ServingEpoch": None, "EffectiveStatus": "draft",
            })
        return _Result()

    _install_engine(monkeypatch, dispatch)
    monkeypatch.setattr(
        publication._r_qdrant, "update_qdrant_metadata",
        lambda *_args, **_kwargs: pytest.fail("invalid event must not change Qdrant"),
    )
    event = {
        "OutboxID": 353, "DocID": 170, "Action": "new_variant",
        "PayloadJson": '{"reviewer":"reviewer_b","reviewer_id":100,"reviewer_roles":["reviewer"]}',
    }

    with pytest.raises(RuntimeError, match="biến thể"):
        publication._publish_event(event)


def test_new_variant_holds_database_lock_through_publication(monkeypatch):
    order = []

    def dispatch(sql, _params):
        if "SELECT BaseCode, VariantCode" in sql:
            return _Result(mapped={"BaseCode": "QA-PUMP", "VariantCode": "A"})
        if "sp_getapplock" in sql:
            order.append("locked")
            return _Result(row=(0,))
        return _Result()

    _install_engine(monkeypatch, dispatch)
    monkeypatch.setattr(
        publication, "_publish_event_unlocked",
        lambda *_args, **_kwargs: order.append("published") or "done",
        raising=False,
    )

    result = publication._publish_event({"DocID": 170, "Action": "new_variant"})

    assert result == "done"
    assert order == ["locked", "published"]


def test_database_publication_failure_hides_sql_details_from_response(monkeypatch):
    event = {"OutboxID": 353, "DocID": 170, "Action": "new_variant", "AttemptCount": 1}
    monkeypatch.setattr(publication, "_claim_outbox", lambda **_kwargs: dict(event))
    monkeypatch.setattr(
        publication,
        "_publish_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            IntegrityError("UPDATE secret_table SET password=:password", {"password": "secret"}, Exception("constraint"))
        ),
    )
    monkeypatch.setattr(publication, "_mark_outbox_failure", lambda *_args, **_kwargs: None)

    result = publication.process_publication_outbox_once(outbox_id=353)

    assert result.state == "failed"
    assert "secret_table" not in result.error
    assert "secret" not in result.error


def test_publish_document_creates_idempotent_outbox_and_processes_it(monkeypatch):
    _install_validations(monkeypatch)

    def dispatch(sql, _params):
        if "SELECT PublicationState, Servable, PublicationVersion" in sql:
            return _Result(
                mapped={
                    "PublicationState": "draft",
                    "Servable": False,
                    "PublicationVersion": 4,
                }
            )
        if "SELECT TOP 1 OutboxID, Status" in sql:
            return _Result(mapped=None)
        if "INSERT INTO dbo.PublicationOutbox" in sql:
            return _Result(row=(901,))
        return _Result()

    fake_engine = _install_engine(monkeypatch, dispatch)
    processed = []

    def process_once(**kwargs):
        processed.append(kwargs)
        return publication.PublicationResult(
            ok=True,
            doc_id=42,
            outbox_id=901,
            state="published",
        )

    monkeypatch.setattr(publication, "process_publication_outbox_once", process_once)

    result = publication.publish_document(
        42,
        action="NEW_VERSION",
        reviewer="Approver",
        reviewer_id=11,
        reviewer_roles=["Reviewer", "knowledge_approver", "Reviewer"],
    )

    assert result.ok is True
    assert processed == [{"outbox_id": 901, "worker_id": "inline:Approver"}]
    publishing_update = next(
        params
        for sql, params in fake_engine.calls
        if "SET PublicationState = 'publishing'" in sql
    )
    assert publishing_update == {"doc_id": 42}
    insert_params = next(
        params
        for sql, params in fake_engine.calls
        if "INSERT INTO dbo.PublicationOutbox" in sql
    )
    assert insert_params["doc_id"] == 42
    assert insert_params["action"] == "new_version"
    assert insert_params["key"] == "publication:42:new_version:5"
    assert json.loads(insert_params["payload"]) == {
        "reviewer": "Approver",
        "reviewer_id": 11,
        "reviewer_roles": ["knowledge_approver", "reviewer"],
        "action": "new_version",
    }


@pytest.mark.parametrize(
    ("state", "servable", "existing", "expected_state", "expected_outbox"),
    [
        ("published", True, None, "published", None),
        ("publishing", False, {"OutboxID": 77, "Status": "processing"}, "processing", 77),
    ],
)
def test_publish_document_is_idempotent_for_published_or_processing_work(
    monkeypatch,
    state,
    servable,
    existing,
    expected_state,
    expected_outbox,
):
    _install_validations(monkeypatch)

    def dispatch(sql, _params):
        if "SELECT PublicationState, Servable, PublicationVersion" in sql:
            return _Result(
                mapped={
                    "PublicationState": state,
                    "Servable": servable,
                    "PublicationVersion": 2,
                }
            )
        if "SELECT TOP 1 OutboxID, Status" in sql:
            return _Result(mapped=existing)
        return _Result()

    fake_engine = _install_engine(monkeypatch, dispatch)
    monkeypatch.setattr(
        publication,
        "process_publication_outbox_once",
        lambda **_kwargs: pytest.fail("idempotent result must not process again"),
    )

    result = publication.publish_document(
        42,
        reviewer_id=11,
        reviewer_roles=["reviewer"],
    )

    assert result.ok is True
    assert result.state == expected_state
    assert result.outbox_id == expected_outbox
    if expected_state == "published":
        assert all("PublicationOutbox" not in sql for sql, _params in fake_engine.calls)
    else:
        assert all(
            "SET Status = 'pending', PayloadJson" not in sql
            for sql, _params in fake_engine.calls
        )


def test_publish_document_requeues_existing_failed_event_with_fresh_actor_payload(
    monkeypatch,
):
    _install_validations(monkeypatch)

    def dispatch(sql, _params):
        if "SELECT PublicationState, Servable, PublicationVersion" in sql:
            return _Result(
                mapped={
                    "PublicationState": "failed",
                    "Servable": False,
                    "PublicationVersion": 3,
                }
            )
        if "SELECT TOP 1 OutboxID, Status" in sql:
            return _Result(mapped={"OutboxID": 88, "Status": "failed"})
        return _Result()

    fake_engine = _install_engine(monkeypatch, dispatch)
    monkeypatch.setattr(
        publication,
        "process_publication_outbox_once",
        lambda **kwargs: publication.PublicationResult(
            ok=False,
            doc_id=42,
            outbox_id=kwargs["outbox_id"],
            state="failed",
        ),
    )

    result = publication.publish_document(
        42,
        reviewer="retry-user",
        reviewer_id=11,
        reviewer_roles=["reviewer"],
    )

    assert result.state == "failed"
    requeue_params = next(
        params
        for sql, params in fake_engine.calls
        if "SET Status = 'pending', PayloadJson" in sql
    )
    assert requeue_params["outbox_id"] == 88
    assert json.loads(requeue_params["payload"])["reviewer"] == "retry-user"


def test_process_outbox_claims_available_event_with_worker_and_attempt_limit(
    monkeypatch,
):
    event = {
        "OutboxID": 71,
        "DocID": 42,
        "Action": "standalone",
        "PayloadJson": "{}",
        "AttemptCount": 2,
    }

    def dispatch(sql, _params):
        if "SELECT TOP 1 OutboxID" in sql:
            return _Result(row=(71,))
        if "OUTPUT INSERTED.OutboxID, INSERTED.DocID" in sql:
            return _Result(mapped=event)
        return _Result()

    fake_engine = _install_engine(monkeypatch, dispatch)
    seen = []
    monkeypatch.setattr(
        publication,
        "_publish_event",
        lambda claimed: seen.append(dict(claimed))
        or publication.PublicationResult(True, int(claimed["DocID"]), state="published"),
    )

    result = publication.process_publication_outbox_once(worker_id="worker-a")

    assert result.state == "published"
    assert seen == [event]
    claim_params = next(
        params
        for sql, params in fake_engine.calls
        if "OUTPUT INSERTED.OutboxID, INSERTED.DocID" in sql
    )
    assert claim_params == {
        "worker": "worker-a",
        "outbox_id": 71,
        "max_attempts": publication.MAX_PUBLICATION_ATTEMPTS,
    }
    assert any(
        params == {"doc_id": 42}
        for sql, params in fake_engine.calls
        if "SET PublicationState = 'publishing'" in sql
    )


def test_process_outbox_returns_none_when_no_event_is_available(monkeypatch):
    fake_engine = _install_engine(monkeypatch, lambda _sql, _params: _Result())

    result = publication.process_publication_outbox_once()

    assert result is None
    select_params = next(
        params
        for sql, params in fake_engine.calls
        if "SELECT TOP 1 OutboxID" in sql
    )
    assert select_params == {"max_attempts": publication.MAX_PUBLICATION_ATTEMPTS}


def test_process_new_version_event_updates_qdrant_sql_audit_and_cache(monkeypatch):
    _install_validations(monkeypatch)
    event = {
        "OutboxID": 901,
        "DocID": 42,
        "Action": "new_version",
        "PayloadJson": json.dumps(
            {
                "reviewer": "Approver",
                "reviewer_id": 11,
                "reviewer_roles": ["reviewer"],
            }
        ),
        "AttemptCount": 1,
    }

    def dispatch(sql, _params):
        if "SELECT DocID, BaseCode, VariantCode" in sql:
            return _Result(
                mapped={
                    "DocID": 42,
                    "BaseCode": "PUMP",
                    "VariantCode": None,
                    "PublicationVersion": 4,
                    "ServingEpoch": 33,
                    "EffectiveStatus": "draft",
                }
            )
        if "SELECT DocID, ServingEpoch" in sql:
            return _Result(rows=[(41, 77, "effective")])
        return _Result()

    fake_engine = _install_engine(monkeypatch, dispatch)
    monkeypatch.setattr(publication, "_claim_outbox", lambda **_kwargs: dict(event))
    staging_updates = []
    batch_updates = []
    audit_calls = []
    cache_calls = []
    monkeypatch.setattr(
        publication._r_qdrant,
        "update_qdrant_metadata",
        lambda doc_id, metadata, require_points: staging_updates.append(
            (doc_id, dict(metadata), require_points)
        )
        or True,
    )
    monkeypatch.setattr(
        publication._r_qdrant,
        "batch_update_qdrant_metadata",
        lambda updates, require_points: batch_updates.append(
            ({key: dict(value) for key, value in updates.items()}, require_points)
        )
        or True,
    )
    monkeypatch.setattr(
        publication._r_audit,
        "write_audit_log",
        lambda *args: audit_calls.append(args),
    )
    monkeypatch.setattr(
        publication._r_semantic_cache,
        "_invalidate_semantic_cache",
        cache_calls.append,
    )

    result = publication.process_publication_outbox_once(
        outbox_id=901,
        worker_id="worker-a",
    )

    assert result.to_dict() == {
        "ok": True,
        "doc_id": 42,
        "outbox_id": 901,
        "state": "published",
        "error": None,
        "validation": None,
    }
    assert staging_updates == [
        (
            42,
            {
                "servable": False,
                "publication_state": "qdrant_synced",
                "publication_version": 5,
                "serving_epoch": 901,
            },
            True,
        )
    ]
    assert len(batch_updates) == 1
    activated, require_points = batch_updates[0]
    assert require_points is True
    assert activated[41] == {
        "servable": False,
        "is_current": False,
        "is_archived": True,
        "lifecycle_status": "superseded",
        "effective_status": "superseded",
        "publication_state": "published",
    }
    assert activated[42]["servable"] is True
    assert activated[42]["effective_status"] == "effective"
    assert activated[42]["supersedes_doc_id"] == 41
    assert activated[42]["publication_version"] == 5
    final_sql, final_params = next(
        (sql, params)
        for sql, params in fake_engine.calls
        if "SET IsCurrent = 1, IsArchived = 0, Servable = 1" in sql
    )
    assert "EffectiveStatus = 'effective'" in final_sql
    assert final_params == {
        "doc_id": 42,
        "reviewer": "Approver",
        "old_id": 41,
        "publication_version": 5,
        "serving_epoch": 901,
    }
    supersede_sql = next(
        sql
        for sql, _params in fake_engine.calls
        if "SET IsCurrent = 0, IsArchived = 1, Servable = 0" in sql
    )
    assert "EffectiveStatus = 'superseded'" in supersede_sql
    assert any(
        params == {"outbox_id": 901}
        for sql, params in fake_engine.calls
        if "SET Status = 'done'" in sql
    )
    assert audit_calls == [
        (
            "Approver",
            "publish_new_version",
            "TaiLieu",
            42,
            {
                "old_doc_ids": [41],
                "publication_version": 5,
                "serving_epoch": 901,
                "reviewer_id": 11,
            },
        )
    ]
    assert cache_calls == ["doc.publish"]


def test_process_event_restores_prior_qdrant_visibility_when_sql_finalize_fails(
    monkeypatch,
):
    _install_validations(monkeypatch)
    event = {
        "OutboxID": 902,
        "DocID": 42,
        "Action": "new_version",
        "PayloadJson": '{"reviewer": "Approver", "reviewer_id": 11, "reviewer_roles": ["reviewer"]}',
        "AttemptCount": 2,
    }

    def dispatch(sql, _params):
        if "SELECT DocID, BaseCode, VariantCode" in sql:
            return _Result(
                mapped={
                    "DocID": 42,
                    "BaseCode": "PUMP",
                    "VariantCode": "A",
                    "PublicationVersion": 4,
                    "ServingEpoch": 33,
                    "EffectiveStatus": "draft",
                }
            )
        if "SELECT DocID, ServingEpoch" in sql:
            return _Result(rows=[(41, 77, "effective")])
        if "SET IsCurrent = 1, IsArchived = 0, Servable = 1" in sql:
            raise RuntimeError("SQL finalize failed")
        return _Result()

    _install_engine(monkeypatch, dispatch)
    monkeypatch.setattr(publication, "_claim_outbox", lambda **_kwargs: dict(event))
    monkeypatch.setattr(
        publication._r_qdrant,
        "update_qdrant_metadata",
        lambda *_args, **_kwargs: True,
    )
    batches = []
    monkeypatch.setattr(
        publication._r_qdrant,
        "batch_update_qdrant_metadata",
        lambda updates, require_points: batches.append(
            ({key: dict(value) for key, value in updates.items()}, require_points)
        )
        or True,
    )
    failures = []
    monkeypatch.setattr(
        publication,
        "_mark_outbox_failure",
        lambda claimed, error: failures.append((dict(claimed), str(error))),
    )

    result = publication.process_publication_outbox_once(outbox_id=902)

    assert result.ok is False
    assert result.state == "failed"
    assert result.error == "SQL finalize failed"
    assert failures == [(event, "SQL finalize failed")]
    assert len(batches) == 2
    rollback, require_points = batches[-1]
    assert require_points is False
    assert rollback[41] == {
        "servable": True,
        "is_current": True,
        "is_archived": False,
        "lifecycle_status": "published",
        "effective_status": "effective",
        "publication_state": "published",
        "serving_epoch": 77,
    }
    assert rollback[42] == {
        "servable": False,
        "is_current": False,
        "is_archived": False,
        "effective_status": "draft",
        "publication_state": "qdrant_synced",
        "serving_epoch": 33,
    }


def test_process_failure_disables_qdrant_and_records_retry_state(monkeypatch):
    event = {
        "OutboxID": 93,
        "DocID": 42,
        "Action": "standalone",
        "PayloadJson": "{}",
        "AttemptCount": 3,
    }
    fake_engine = _install_engine(monkeypatch, lambda _sql, _params: _Result())
    monkeypatch.setattr(publication, "_claim_outbox", lambda **_kwargs: dict(event))
    monkeypatch.setattr(
        publication,
        "_publish_event",
        lambda _event: (_ for _ in ()).throw(RuntimeError("qdrant unavailable")),
    )
    qdrant_updates = []
    monkeypatch.setattr(
        publication._r_qdrant,
        "update_qdrant_metadata",
        lambda *args, **kwargs: qdrant_updates.append((args, kwargs)) or True,
    )

    result = publication.process_publication_outbox_once(outbox_id=93)

    assert not result
    assert result.error == "qdrant unavailable"
    assert qdrant_updates == [
        ((42, {"servable": False, "publication_state": "failed"}), {})
    ]
    outbox_params = next(
        params
        for sql, params in fake_engine.calls
        if "UPDATE dbo.PublicationOutbox" in sql
    )
    assert outbox_params == {
        "error": "qdrant unavailable",
        "delay": 8,
        "outbox_id": 93,
    }
    document_params = next(
        params
        for sql, params in fake_engine.calls
        if "SET PublicationState = 'failed', Servable = 0" in sql
    )
    assert document_params == {"error": "qdrant unavailable", "doc_id": 42}


def test_reconcile_publications_counts_success_failure_and_empty_queue(monkeypatch):
    outcomes = iter(
        [
            publication.PublicationResult(True, 1, state="published"),
            publication.PublicationResult(False, 2, state="failed"),
            None,
        ]
    )
    workers = []

    def process_once(**kwargs):
        workers.append(kwargs["worker_id"])
        return next(outcomes)

    monkeypatch.setattr(publication, "process_publication_outbox_once", process_once)

    result = publication.reconcile_publications(limit=10, worker_id="reconciler-a")

    assert result == {"processed": 2, "succeeded": 1, "failed": 1}
    assert workers == ["reconciler-a", "reconciler-a", "reconciler-a"]


def test_reconcile_serving_state_backfills_authoritative_metadata_and_audits(
    monkeypatch,
):
    rows = [
        {
            "DocID": 1,
            "Servable": 1,
            "PublicationState": "published",
            "LifecycleStatus": "published",
            "ReviewStatus": "approved",
            "IsCurrent": 1,
            "IsArchived": 0,
            "PublicationVersion": 3,
            "ServingEpoch": 9,
            "TaxonomyVersion": "v2",
            "ExternalProcessingPolicy": None,
        },
        {
            "DocID": 2,
            "Servable": 0,
            "PublicationState": "failed",
            "LifecycleStatus": "draft",
            "ReviewStatus": "pending",
            "IsCurrent": 0,
            "IsArchived": 1,
            "PublicationVersion": None,
            "ServingEpoch": None,
            "TaxonomyVersion": None,
            "ExternalProcessingPolicy": "internal_only",
        },
    ]

    def dispatch(sql, _params):
        if "FROM dbo.TaiLieu" in sql:
            return _Result(rows=rows)
        return _Result()

    fake_engine = _install_engine(monkeypatch, dispatch)
    qdrant_calls = []

    def update(doc_id, metadata, require_points):
        qdrant_calls.append((doc_id, dict(metadata), require_points))
        return doc_id == 1

    monkeypatch.setattr(publication._r_qdrant, "update_qdrant_metadata", update)
    audits = []
    monkeypatch.setattr(
        publication._r_audit,
        "write_audit_log",
        lambda *args: audits.append(args),
    )

    result = publication.reconcile_serving_state(limit=2, worker_id="w" * 150)

    assert result == {
        "total": 2,
        "updated": 1,
        "failed_doc_ids": [2],
        "worker_id": "w" * 100,
    }
    assert "SELECT TOP (2) DocID" in fake_engine.calls[0][0]
    assert qdrant_calls[0] == (
        1,
        {
            "servable": True,
            "publication_state": "published",
            "lifecycle_status": "published",
            "review_status": "approved",
            "is_current": True,
            "is_archived": False,
            "publication_version": 3,
            "serving_epoch": 9,
            "taxonomy_version": "v2",
            "external_processing_policy": "internal_only",
        },
        True,
    )
    assert qdrant_calls[1][1]["publication_version"] == 1
    assert qdrant_calls[1][1]["serving_epoch"] == 0
    assert qdrant_calls[1][1]["taxonomy_version"] == "v1"
    assert qdrant_calls[1][1]["external_processing_policy"] == "internal_only"
    assert audits == [
        (
            "w" * 100,
            "reconcile_qdrant_serving_state",
            "TaiLieu",
            None,
            {"total": 2, "updated": 1, "failed_doc_ids": [2]},
        )
    ]
