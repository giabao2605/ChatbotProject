"""Bounded source-snapshot and continuation primitives for GD6.

The module deliberately owns no worker or scheduler.  A request creates a
snapshot, processes one bounded batch, and stores the next position.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from sqlalchemy import text
from mech_chatbot.db.scan_connection import scan_connection


SOURCE_STATES = frozenset({"pending", "checked", "error", "denied"})


class ComprehensiveScanError(RuntimeError):
    """Base error whose message does not disclose protected source details."""


class CursorMismatch(ComprehensiveScanError):
    pass


class SnapshotStale(ComprehensiveScanError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def query_fingerprint(question: str) -> str:
    return fingerprint(" ".join(str(question or "").split()).casefold())


def access_scope_fingerprint(scope: Mapping[str, Any]) -> str:
    normalized = {}
    for key in ("user_id", "username", "roles", "department", "allowed_departments", "allowed_sites", "max_security_level", "site", "global_read_admin"):
        value = scope.get(key)
        if isinstance(value, (set, frozenset, list, tuple)):
            value = sorted(str(item) for item in value)
        normalized[key] = value
    return fingerprint(normalized)


def source_identity(row: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only stable source identity fields; no title/name is returned in cursors."""
    return {
        key: row.get(key)
        for key in ("DocID", "doc_id", "VersionNo", "version_no", "SnapshotID", "snapshot_id", "SourceHash", "source_hash", "Site", "site", "ServingEpoch", "PublicationVersion", "PagesFingerprint", "PageCount", "ExpectedPageCount", "ExtractionComplete", "SecurityLevel", "ExternalProcessingPolicy")
        if row.get(key) is not None
    }


def snapshot_fingerprint(catalog: Sequence[Mapping[str, Any]]) -> str:
    identities = [source_identity(row) for row in catalog]
    identities.sort(key=_canonical)
    return fingerprint(identities)


@dataclass(frozen=True, slots=True)
class CursorBinding:
    scan_id: str
    user_id: int | None
    query_fingerprint: str
    access_scope_fingerprint: str
    search_mode: str
    snapshot_fingerprint: str
    next_offset: int

    def payload(self) -> dict[str, Any]:
        return {
            "scan_id": self.scan_id,
            "user_id": self.user_id,
            "query_fingerprint": self.query_fingerprint,
            "access_scope_fingerprint": self.access_scope_fingerprint,
            "search_mode": self.search_mode,
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "next_offset": self.next_offset,
        }


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def encode_cursor(binding: CursorBinding, secret: str | bytes) -> str:
    key = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    if not key:
        raise ValueError("cursor signing secret is required")
    body = _b64(_canonical(binding.payload()).encode("utf-8"))
    signature = _b64(hmac.new(key, body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{signature}"


def decode_cursor(value: str, secret: str | bytes) -> CursorBinding:
    try:
        body, signature = str(value).split(".", 1)
        key = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
        expected = _b64(hmac.new(key, body.encode("ascii"), hashlib.sha256).digest())
        if not key or not hmac.compare_digest(signature, expected):
            raise ValueError
        raw = json.loads(_unb64(body).decode("utf-8"))
        binding = CursorBinding(
            scan_id=str(raw["scan_id"]),
            user_id=int(raw["user_id"]) if raw.get("user_id") is not None else None,
            query_fingerprint=str(raw["query_fingerprint"]),
            access_scope_fingerprint=str(raw["access_scope_fingerprint"]),
            search_mode=str(raw["search_mode"]),
            snapshot_fingerprint=str(raw["snapshot_fingerprint"]),
            next_offset=int(raw["next_offset"]),
        )
        if binding.search_mode != "comprehensive" or binding.next_offset < 0:
            raise ValueError
        return binding
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeError):
        raise CursorMismatch("Invalid continuation cursor") from None


def assert_cursor_binding(
    binding: CursorBinding,
    *,
    user_id: int | None,
    query: str,
    access_scope: Mapping[str, Any],
    snapshot: str,
) -> None:
    expected = CursorBinding(
        scan_id=binding.scan_id,
        user_id=user_id,
        query_fingerprint=query_fingerprint(query),
        access_scope_fingerprint=access_scope_fingerprint(access_scope),
        search_mode="comprehensive",
        snapshot_fingerprint=snapshot,
        next_offset=binding.next_offset,
    )
    if binding.user_id != expected.user_id or binding.query_fingerprint != expected.query_fingerprint or binding.access_scope_fingerprint != expected.access_scope_fingerprint or binding.search_mode != expected.search_mode:
        raise CursorMismatch("Continuation cursor does not match this request")
    if binding.snapshot_fingerprint != expected.snapshot_fingerprint:
        raise SnapshotStale("Source snapshot changed; restart the scan")


def _row_mapping(row: Any) -> Mapping[str, Any]:
    return row._mapping if hasattr(row, "_mapping") else row


@dataclass(frozen=True, slots=True)
class ScanBatch:
    scan_id: str
    snapshot_fingerprint: str
    rows: tuple[Mapping[str, Any], ...]
    next_offset: int
    total: int
    counts: Mapping[str, int]
    complete: bool
    binding: CursorBinding
    results: tuple[Mapping[str, Any], ...] = ()


class ComprehensiveScanStore:
    """Small SQL repository; callers supply fresh ACL-filtered catalog rows."""

    def __init__(self, engine: Any, *, cursor_secret: str | bytes, catalog_loader: Callable[..., Sequence[Mapping[str, Any]]]):
        self.engine = engine
        self.cursor_secret = cursor_secret
        self.catalog_loader = catalog_loader

    def start(self, *, user_id: int | None, username: str | None, question: str, access_scope: Mapping[str, Any], batch_size: int = 10) -> tuple[str, CursorBinding]:
        catalog = tuple(self.catalog_loader(**dict(access_scope)))
        snapshot = snapshot_fingerprint(catalog)
        scan_id = secrets.token_urlsafe(24)
        now = datetime.now(timezone.utc)
        query_fp = query_fingerprint(question)
        scope_fp = access_scope_fingerprint({**access_scope, "user_id": user_id, "username": username})
        with scan_connection(self.engine) as conn:
            conn.execute(text("""INSERT INTO dbo.RagComprehensiveScan
                (ScanID,UserID,Username,QueryFingerprint,AccessScopeFingerprint,SearchMode,SnapshotFingerprint,Status,NextOffset,TotalSources,CheckedCount,ErrorCount,CreatedAt,UpdatedAt)
                VALUES (:id,:uid,:name,:q,:scope,'comprehensive',:snap,'active',0,:total,0,0,:now,:now)"""),
                {"id": scan_id, "uid": user_id, "name": username, "q": query_fp, "scope": scope_fp, "snap": snapshot, "total": len(catalog), "now": now})
            for ordinal, row in enumerate(catalog):
                conn.execute(text("""INSERT INTO dbo.RagComprehensiveSource
                    (ScanID,SourceOrdinal,DocID,VersionNo,SourceIdentityJson,Status,PageOffset,TextOffset)
                    VALUES (:id,:ord,:doc,:version,:identity,'pending',0,0)"""), {
                        "id": scan_id, "ord": ordinal, "doc": row.get("DocID", row.get("doc_id")),
                        "version": row.get("VersionNo", row.get("version_no")), "identity": _canonical(source_identity(row))})
        return scan_id, CursorBinding(scan_id, user_id, query_fp, scope_fp, "comprehensive", snapshot, 0)

    def _validate(self, binding, *, user_id, question, access_scope, reauthorize=None):
        # Validate request identity before loading any protected catalog or saved evidence.
        assert_cursor_binding(binding, user_id=user_id, query=question,
                              access_scope=access_scope, snapshot=binding.snapshot_fingerprint)
        if reauthorize is not None and not reauthorize():
            raise CursorMismatch("Access scope changed; restart the scan")
        snapshot = snapshot_fingerprint(tuple(self.catalog_loader(**dict(access_scope))))
        if snapshot != binding.snapshot_fingerprint:
            # Commit invalidation separately: raising inside begin() would roll it back.
            with scan_connection(self.engine) as conn:
                conn.execute(text("UPDATE dbo.RagComprehensiveScan SET Status='stale',UpdatedAt=:now WHERE ScanID=:id"),
                             {"id":binding.scan_id,"now":datetime.now(timezone.utc)})
            raise SnapshotStale("Source snapshot changed; restart the scan")

    def _scan(self, conn, binding):
        lock = " WITH (UPDLOCK,HOLDLOCK)" if conn.dialect.name == "mssql" else ""
        row = conn.execute(text(f"SELECT * FROM dbo.RagComprehensiveScan{lock} WHERE ScanID=:id"),
                           {"id":binding.scan_id}).mappings().first()
        if not row:
            raise CursorMismatch("Invalid continuation cursor")
        if (row["UserID"] != binding.user_id or row["QueryFingerprint"] != binding.query_fingerprint
                or row["AccessScopeFingerprint"] != binding.access_scope_fingerprint
                or row["SearchMode"] != binding.search_mode):
            raise CursorMismatch("Continuation cursor does not match this request")
        if row["Status"] == "stale" or row["SnapshotFingerprint"] != binding.snapshot_fingerprint:
            raise SnapshotStale("Source snapshot changed; restart the scan")
        if binding.next_offset > int(row["NextOffset"]):
            raise CursorMismatch("Invalid continuation cursor position")
        return row

    @staticmethod
    def _binding(row):
        return CursorBinding(row["ScanID"], row["UserID"], row["QueryFingerprint"],
                             row["AccessScopeFingerprint"], row["SearchMode"],
                             row["SnapshotFingerprint"], int(row["NextOffset"]))

    def load_batch(self, binding: CursorBinding, *, user_id, question, access_scope,
                   batch_size: int = 10, reauthorize=None) -> ScanBatch:
        if not 1 <= batch_size <= 100:
            raise ValueError("batch_size must be between 1 and 100")
        self._validate(binding, user_id=user_id, question=question,
                       access_scope=access_scope, reauthorize=reauthorize)
        with scan_connection(self.engine) as conn:
            row = self._scan(conn, binding)
            counts = {state: 0 for state in SOURCE_STATES}
            for status, count in conn.execute(text("SELECT Status,COUNT(*) FROM dbo.RagComprehensiveSource WHERE ScanID=:id GROUP BY Status"),
                                              {"id":binding.scan_id}):
                counts[status] = int(count)
            limit = 'LIMIT :limit' if conn.dialect.name == 'sqlite' else 'OFFSET 0 ROWS FETCH NEXT :limit ROWS ONLY'
            pending = tuple(dict(source) for source in conn.execute(text(f"""SELECT SourceOrdinal,DocID,VersionNo,
                SourceIdentityJson,Status,PageOffset,TextOffset,ErrorCode FROM dbo.RagComprehensiveSource
                WHERE ScanID=:id AND Status='pending' ORDER BY PageOffset,TextOffset,SourceOrdinal {limit}"""),
                {"id":binding.scan_id,"limit":batch_size}).mappings())
            complete = counts["checked"] == int(row["TotalSources"])
            current = self._binding(row)
        return ScanBatch(binding.scan_id, binding.snapshot_fingerprint, pending, current.next_offset,
                         int(row["TotalSources"]), counts, complete, current)

    def get_results(self, binding: CursorBinding, *, result_revision=None, **authorization) -> ScanBatch:
        batch = self.load_batch(binding, **authorization)
        revision = batch.next_offset if result_revision is None else result_revision
        if type(revision) is not int or not 0 <= revision <= batch.next_offset:
            raise CursorMismatch("Invalid result revision")
        with scan_connection(self.engine) as conn:
            self._scan(conn, binding)
            raw = conn.execute(text("SELECT ResultsJson FROM dbo.RagComprehensiveBatch WHERE ScanID=:id AND Revision=:revision"),
                               {"id":binding.scan_id,"revision":revision}).scalar()
        return replace(batch, results=tuple(json.loads(raw)) if raw else ())

    def save_batch(self, binding: CursorBinding, updates: Sequence[Mapping[str, Any]], *,
                   user_id, question, access_scope, reauthorize=None) -> CursorBinding:
        self._validate(binding, user_id=user_id, question=question,
                       access_scope=access_scope, reauthorize=reauthorize)
        with scan_connection(self.engine) as conn:
            row = self._scan(conn, binding)
            if binding.next_offset != int(row["NextOffset"]) or not updates:
                return self._binding(row)
            # Acquire a write lock through CAS before touching per-source rows; a losing
            # request replays committed progress instead of appending duplicate evidence.
            changed = conn.execute(text("""UPDATE dbo.RagComprehensiveScan
                SET NextOffset=NextOffset+1,UpdatedAt=:now
                WHERE ScanID=:id AND NextOffset=:offset AND Status='active'"""),
                {"id":binding.scan_id,"offset":binding.next_offset,"now":datetime.now(timezone.utc)})
            if changed.rowcount != 1:
                return self._binding(self._scan(conn, binding))
            seen, batch_results = set(), []
            for update in updates:
                ordinal, status = int(update["SourceOrdinal"]), update["status"]
                if ordinal in seen or status not in SOURCE_STATES:
                    raise ValueError("Invalid comprehensive source update")
                seen.add(ordinal)
                source = conn.execute(text("SELECT * FROM dbo.RagComprehensiveSource WHERE ScanID=:id AND SourceOrdinal=:ordinal"),
                                      {"id":binding.scan_id,"ordinal":ordinal}).mappings().first()
                offset = int(update.get("page_offset", 0))
                text_offset = int(update.get("text_offset", 0))
                if not source or source["Status"] != "pending" or offset < int(source["PageOffset"]):
                    raise ValueError("Invalid comprehensive source progress")
                if text_offset < 0 or (offset == int(source['PageOffset']) and text_offset < int(source['TextOffset'])):
                    raise ValueError("Invalid comprehensive passage progress")
                batch_results.extend(update.get("results") or [])
                conn.execute(text("""UPDATE dbo.RagComprehensiveSource SET Status=:status,PageOffset=:offset,TextOffset=:text_offset,
                    ErrorCode=:error,CheckedAt=:now WHERE ScanID=:id AND SourceOrdinal=:ordinal"""),
                    {"id":binding.scan_id,"ordinal":ordinal,"status":status,"offset":offset,"text_offset":text_offset,
                     "error":update.get("error_code"),"now":datetime.now(timezone.utc)})
            conn.execute(text("INSERT INTO dbo.RagComprehensiveBatch (ScanID,Revision,ResultsJson) VALUES (:id,:revision,:results)"),
                {"id":binding.scan_id,"revision":binding.next_offset+1,"results":_canonical(batch_results)})
            counts = {state:int(conn.execute(text("SELECT COUNT(*) FROM dbo.RagComprehensiveSource WHERE ScanID=:id AND Status=:state"),
                                          {"id":binding.scan_id,"state":state}).scalar()) for state in SOURCE_STATES}
            status = "complete" if counts["checked"] == int(row["TotalSources"]) else "active"
            conn.execute(text("""UPDATE dbo.RagComprehensiveScan SET CheckedCount=:checked,ErrorCount=:errors,Status=:status
                WHERE ScanID=:id"""), {"id":binding.scan_id,"checked":counts["checked"],"errors":counts["error"],"status":status})
            return self._binding(self._scan(conn, binding))


__all__ = ["SOURCE_STATES", "ComprehensiveScanError", "CursorMismatch", "SnapshotStale", "CursorBinding", "ScanBatch", "ComprehensiveScanStore", "fingerprint", "query_fingerprint", "access_scope_fingerprint", "source_identity", "snapshot_fingerprint", "encode_cursor", "decode_cursor", "assert_cursor_binding"]
