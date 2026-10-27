"""Pending-only proposals and explicit human review for Graph v2 records."""

from decimal import Decimal, InvalidOperation
import json

from sqlalchemy import text

from ..engine import _ensure_engine, engine
from .graph_v2 import SqlGraphV2Reader, _ELIGIBLE_DOCUMENTS, _EDGE_BOM_QUOTE_PROOF, _bom_row_proof
from .structured_evidence import _reviewer, _table


_SPECS = {
    "binding": ("ReviewedAssemblyBindings", "BindingID", {
        "part_key": "PartKey", "doc_id": "DocID", "version_no": "VersionNo",
        "source_doc_id": "SourceDocID", "source_version": "SourceVersion",
        "source_page": "SourcePage", "source_quote": "SourceQuote",
    }),
    "occurrence": ("ReviewedGraphOccurrences", "OccurrenceID", {
        "occurrence_id": "OccurrenceID", "edge_id": "EdgeID", "parent_doc_id": "ParentDocID",
        "parent_version": "ParentVersion", "row_key": "RowKey", "part_key": "PartKey",
        "quantity": "Quantity", "unit": "Unit", "quantity_basis": "QuantityBasis",
        "child_binding_id": "ChildBindingID", "is_leaf": "IsLeaf",
        "source_page": "SourcePage", "source_quote": "SourceQuote",
    }),
    "declaration": ("ReviewedGraphDeclarations", "DeclarationID", {
        "doc_id": "DocID", "version_no": "VersionNo", "snapshot_id": "SnapshotID",
        "rows_complete": "RowsComplete", "tree_complete": "TreeComplete",
        "source_page": "SourcePage", "source_quote": "SourceQuote",
        "expected_rows": "ExpectedRows", "source_hash": "SourceHash",
    }),
}
_POLICY = _ELIGIBLE_DOCUMENTS.split(", SafeOccurrences AS", 1)[0]


def _spec(kind):
    if kind not in _SPECS:
        raise ValueError("kind must be binding, occurrence or declaration")
    return _SPECS[kind]


def _positive(value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("positive integer required")
    return value


def _payload(kind, payload):
    columns = _spec(kind)[2]
    if set(payload) != set(columns):
        raise ValueError("record fields must match the versioned schema")
    values = dict(payload)
    for key, value in values.items():
        if key in {"is_leaf", "rows_complete", "tree_complete"}:
            if not isinstance(value, bool):
                raise ValueError("explicit boolean required")
        elif key == "expected_rows":
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("nonnegative expected rows required")
        elif key == "child_binding_id" and value is None:
            continue
        elif key in {"doc_id", "source_doc_id", "parent_doc_id", "edge_id", "child_binding_id",
                     "source_version", "parent_version", "version_no", "source_page"}:
            _positive(value)
        elif not isinstance(value, str) or not value.strip():
            raise ValueError("nonempty string required")
    limits = {"source_quote": 2000, "part_key": 255, "row_key": 255,
              "occurrence_id": 200, "snapshot_id": 128, "quantity": 128, "unit": 64}
    if any(len(values[key]) > limit for key, limit in limits.items() if key in values):
        raise ValueError("field exceeds schema length")
    if "part_key" in values and not values["part_key"].startswith("part:"):
        raise ValueError("canonical part key required")
    if kind == "occurrence":
        try:
            quantity = Decimal(values["quantity"])
        except InvalidOperation:
            raise ValueError("invalid occurrence quantity") from None
        if not quantity.is_finite() or quantity < 0 or values["quantity_basis"] != "per_parent":
            raise ValueError("finite per-parent quantity required")
        if values["is_leaf"] and values["child_binding_id"] is not None:
            raise ValueError("leaf cannot have child binding")
    if kind == "declaration":
        hash_value = values["source_hash"]
        if len(hash_value) != 64 or any(char not in "0123456789abcdef" for char in hash_value.lower()):
            raise ValueError("SHA-256 source hash required")
        if values["tree_complete"] and not values["rows_complete"]:
            raise ValueError("tree completeness requires row completeness")
    return values


def _proof(conn, kind, values, params):
    if kind == "binding":
        doc_id, version = values["source_doc_id"], values["source_version"]
    elif kind == "occurrence":
        doc_id, version = values["parent_doc_id"], values["parent_version"]
    else:
        doc_id, version = values["doc_id"], values["version_no"]
    proof_params = {**params, **values, "proof_doc": doc_id, "proof_version": version}
    target_check = ""
    if kind == "binding":
        if not values.get('source_hash') or not values.get('snapshot_id'):
            return False
        lock = ' WITH (UPDLOCK,HOLDLOCK)' if conn.dialect.name != 'sqlite' else ''
        target_check = """AND EXISTS (SELECT 1 FROM EligibleDocuments target
                           WHERE target.DocID=:doc_id AND target.VersionNo=:version_no)""" + f"""
          AND EXISTS (SELECT 1 FROM dbo.StructuredSourceSnapshot pinned{lock}
            WHERE pinned.DocID=:source_doc_id AND pinned.VersionNo=:source_version
              AND pinned.SourceHash=:source_hash AND pinned.SnapshotID=:snapshot_id
              AND pinned.CurrentSnapshot=1)"""
    elif kind == "occurrence":
        target_check = """AND EXISTS (
          SELECT 1 FROM dbo.KnowledgeGraphEdge e
          JOIN dbo.KnowledgeGraphNode sn ON sn.NodeID=e.SourceNodeID
          JOIN dbo.KnowledgeGraphNode tn ON tn.NodeID=e.TargetNodeID
          JOIN dbo.BangKeVatTu bom ON bom.DocID=e.SourceDocID AND bom.TrangSo=:source_page
          WHERE e.EdgeID=:edge_id AND e.ServingStatus='approved'
            AND e.RelationType='CONTAINS_PART' AND e.SourceDocID=:parent_doc_id
            AND e.SourceVersion=:parent_version AND e.SourcePage>0
            AND sn.CanonicalKey='document:'+CAST(:parent_doc_id AS NVARCHAR(30))
            AND tn.CanonicalKey=:part_key
            AND 'part:'+LOWER(LTRIM(RTRIM(bom.MaHang)))=:part_key
            AND """ + _bom_row_proof(row_key=':row_key', full_row_key=':quantity_row_key',
                part_key=':part_key', page=':source_page', quantity=':quantity', unit=':unit') + """
            AND CHARINDEX(:source_quote,COALESCE(bom.RawRowJson,N''))>0
        """ + _EDGE_BOM_QUOTE_PROOF + """
        ) AND EXISTS (
          SELECT 1 FROM dbo.StructuredNumericFact fact
          JOIN dbo.StructuredEvidenceScope scope ON scope.ScopeID=fact.ScopeID
          JOIN dbo.StructuredSourceSnapshot pinned ON pinned.DocID=fact.SourceDocID
            AND pinned.VersionNo=fact.SourceVersion AND pinned.SnapshotID=scope.SnapshotID
            AND pinned.SourceHash=fact.SourceHash AND pinned.CurrentSnapshot=1
          WHERE fact.SourceDocID=:parent_doc_id AND fact.SourceVersion=:parent_version
            AND fact.ReviewStatus='approved' AND fact.ReviewedBy IS NOT NULL
            AND JSON_VALUE(fact.ReviewedPayloadJson,'$.locator.page')=CAST(:source_page AS NVARCHAR(30))
            AND JSON_VALUE(fact.ReviewedPayloadJson,'$.locator.row_key')=:quantity_row_key
            AND JSON_VALUE(fact.ReviewedPayloadJson,'$.value')=:quantity
            AND JSON_VALUE(fact.ReviewedPayloadJson,'$.unit')=:unit
        ) AND (:child_binding_id IS NULL OR EXISTS (
          SELECT 1 FROM dbo.ReviewedAssemblyBindings binding
          JOIN EligibleDocuments child ON child.DocID=binding.DocID AND child.VersionNo=binding.VersionNo
          JOIN EligibleDocuments source ON source.DocID=binding.SourceDocID AND source.VersionNo=binding.SourceVersion
          JOIN dbo.StructuredSourceSnapshot binding_pin ON binding_pin.DocID=binding.SourceDocID
            AND binding_pin.VersionNo=binding.SourceVersion AND binding_pin.SourceHash=binding.SourceHash
            AND binding_pin.SnapshotID=binding.SnapshotID AND binding_pin.CurrentSnapshot=1
          WHERE binding.BindingID=:child_binding_id AND binding.ReviewStatus='approved'
            AND binding.ReviewedBy IS NOT NULL AND binding.ReviewedAt IS NOT NULL
            AND binding.PartKey=:part_key AND binding.ProductScope=:product_scope AND binding.Site=:site
        ))"""
    else:
        target_check = """AND EXISTS (SELECT 1 FROM dbo.StructuredSourceSnapshot pinned
          WHERE pinned.DocID=:doc_id AND pinned.VersionNo=:version_no
            AND pinned.SnapshotID=:snapshot_id AND pinned.SourceHash=:source_hash
            AND pinned.CurrentSnapshot=1)"""
    if kind == 'occurrence':
        proof_params['quantity_row_key'] = f"page-{values['source_page']}:{values['row_key']}"
    row = conn.execute(text(_POLICY + """
        SELECT t.DocID FROM EligibleDocuments t
        WHERE t.DocID=:proof_doc AND t.VersionNo=:proof_version
          AND (EXISTS (SELECT 1 FROM dbo.DocumentPages p
            WHERE p.DocID=t.DocID AND p.PageNo=:source_page
              AND (CHARINDEX(:source_quote,COALESCE(p.TextExtract,N''))>0
                OR CHARINDEX(:source_quote,COALESCE(p.LocalOCRText,N''))>0
                OR CHARINDEX(:source_quote,COALESCE(p.VisionSummary,N''))>0))
            OR EXISTS (SELECT 1 FROM dbo.BangKeVatTu bom
              WHERE bom.DocID=t.DocID AND bom.TrangSo=:source_page
                AND CHARINDEX(:source_quote,COALESCE(bom.RawRowJson,N''))>0))
        """ + target_check), proof_params).first()
    return bool(row)


def _params(access_context, product_scope, site):
    if not isinstance(product_scope, str) or not product_scope.strip() or len(product_scope) > 255:
        raise ValueError("valid product scope required")
    if not isinstance(site, str) or not site.strip() or len(site) > 100:
        raise ValueError("valid site required")
    return SqlGraphV2Reader(None, product_scope=product_scope, site=site,
                            access_context=access_context).params


def _actor(conn, user_id, kind, values):
    user = conn.execute(text(f"SELECT Username,IsActive FROM {_table(conn, 'Users')} WHERE UserID=:uid"),
                        {"uid": user_id}).mappings().first()
    if not user or not user["IsActive"]:
        raise PermissionError("Verified reviewer required")
    profile = {"user_id": user_id, "username": user["Username"]}
    doc_ids = ((values["source_doc_id"],) if kind == "binding" else
               (values["parent_doc_id"],) if kind == "occurrence" else (values["doc_id"],))
    for doc_id in set(doc_ids):
        source = conn.execute(text(f"SELECT KnowledgeApproverUserID FROM {_table(conn, 'TaiLieu')} WHERE DocID=:doc"),
                              {"doc": doc_id}).mappings().first()
        if not source:
            raise PermissionError("Evidence source unavailable")
        _reviewer(conn, source, profile)
    return profile


def _note(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise ValueError("nonempty review note required")
    return value.strip()


def _audit(conn, profile, kind, record_id, action, before, after, note):
    conn.execute(text(f"INSERT INTO {_table(conn, 'AuditLog')} (UserID,Username,Action,EntityType,EntityID,Details) "
                      "VALUES (:uid,:username,:action,'GraphV2',NULL,:details)"), {
        "uid": profile["user_id"], "username": profile["username"],
        "action": f"graph_v2_{kind}_{action}", "details": json.dumps({
            "record_id": record_id, "before": before, "after": after, "note": note,
        }, ensure_ascii=False, default=str),
    })


def _binding_source_pin(conn, values):
    lock = ' WITH (UPDLOCK,HOLDLOCK)' if conn.dialect.name != 'sqlite' else ''
    row = conn.execute(text(f"SELECT SourceHash,SnapshotID FROM dbo.StructuredSourceSnapshot{lock} "
        "WHERE DocID=:source_doc_id AND VersionNo=:source_version AND CurrentSnapshot=1"), values).mappings().first()
    return {'source_hash':row['SourceHash'], 'snapshot_id':row['SnapshotID']} if row else None


def propose_graph_v2_record(kind, payload, *, actor_id, access_context, product_scope, site, note):
    """Persist a pending proposal; explicit review is the only serving path."""
    _positive(actor_id)
    note = _note(note)
    values = _payload(kind, payload)
    params = _params(access_context, product_scope, site)
    table, id_column, columns = _spec(kind)
    stored_columns = {**columns, "product_scope": "ProductScope"}
    if kind != "declaration":
        stored_columns = {**stored_columns, "site": "Site"}
    _ensure_engine()
    with engine.begin() as conn:
        actor = _actor(conn, actor_id, kind, values)
        if kind == 'binding':
            pin = _binding_source_pin(conn, values)
            if not pin:
                return {"ok": False, "reason": "invalid_provenance"}
            values = {**values, **pin}
            stored_columns = {**stored_columns, 'source_hash':'SourceHash', 'snapshot_id':'SnapshotID'}
        if not _proof(conn, kind, values, params):
            return {"ok": False, "reason": "invalid_provenance"}
        row = conn.execute(text(
            f"INSERT dbo.{table} ({','.join(stored_columns.values())},ReviewStatus) "
            f"OUTPUT INSERTED.{id_column} AS record_id "
            f"VALUES ({','.join(':'+key for key in stored_columns)},'pending')"
        ), {**params, **values}).mappings().first()
        _audit(conn, actor, kind, row["record_id"], "propose", None,
               {**values, "review_status": "pending"}, note)
    return {"ok": True, "record_id": row["record_id"], "status": "pending"}


def revise_graph_v2_occurrence_binding(record_id, child_binding_id, *, reviewer_id,
                                     access_context, product_scope, site, note):
    """Attach a reviewed child to a non-leaf row, then require review again."""
    _positive(reviewer_id)
    _positive(child_binding_id)
    note = _note(note)
    if not isinstance(record_id, str) or not record_id.strip() or len(record_id) > 200:
        raise ValueError("occurrence id required")
    params = {**_params(access_context, product_scope, site), "record_id": record_id,
              "child_binding_id": child_binding_id}
    _ensure_engine()
    with engine.begin() as conn:
        lock = ' WITH (UPDLOCK, ROWLOCK)' if conn.dialect.name != 'sqlite' else ''
        row = conn.execute(text(f"SELECT * FROM dbo.ReviewedGraphOccurrences{lock} "
            "WHERE OccurrenceID=:record_id AND ProductScope=:product_scope AND Site=:site"), params).mappings().first()
        if not row:
            return {"ok": False, "reason": "not_found"}
        values = {key: row[column] for key, column in _spec('occurrence')[2].items()}
        actor = _actor(conn, reviewer_id, 'occurrence', values)
        if (row['ReviewStatus'] != 'approved' or row['ReviewedBy'] is None or row['ReviewedAt'] is None
                or row['ChildBindingID'] is not None or row['IsLeaf']):
            return {"ok": False, "reason": "revision_state_conflict"}
        binding = conn.execute(text(f"SELECT * FROM dbo.ReviewedAssemblyBindings{lock} "
            "WHERE BindingID=:child_binding_id AND ProductScope=:product_scope AND Site=:site"), params).mappings().first()
        if (not binding or binding['ReviewStatus'] != 'approved' or binding['ReviewedBy'] is None
                or binding['ReviewedAt'] is None or binding['PartKey'] != row['PartKey']):
            return {"ok": False, "reason": "invalid_provenance"}
        proof = {key: binding[column] for key, column in _spec('binding')[2].items()}
        proof = {**proof, 'source_hash': binding['SourceHash'], 'snapshot_id': binding['SnapshotID']}
        values = {**values, 'child_binding_id': child_binding_id}
        if not _proof(conn, 'binding', proof, params) or not _proof(conn, 'occurrence', values, params):
            return {"ok": False, "reason": "invalid_provenance"}
        updated = conn.execute(text("UPDATE dbo.ReviewedGraphOccurrences SET ChildBindingID=:child_binding_id,"
            "ReviewStatus='pending',ReviewedBy=NULL,ReviewedAt=NULL "
            "WHERE OccurrenceID=:record_id AND ProductScope=:product_scope AND Site=:site "
            "AND ReviewStatus='approved' AND ChildBindingID IS NULL AND IsLeaf=0"), params)
        if updated.rowcount != 1:
            raise RuntimeError("Occurrence changed during binding revision")
        _audit(conn, actor, 'occurrence', record_id, 'revise_binding', dict(row),
               {**dict(row), 'ChildBindingID': child_binding_id, 'ReviewStatus': 'pending',
                'ReviewedBy': None, 'ReviewedAt': None}, note)
    return {"ok": True, "record_id": record_id, "status": "pending"}


def list_graph_v2_records(kind, status="pending", limit=100, *, access_context, product_scope, site,
                          source_doc_id=None, offset=0):
    table, id_column, columns = _spec(kind)
    if status not in {"pending", "approved", "rejected"}:
        raise ValueError("invalid review status")
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 100000:
        raise ValueError("invalid review offset")
    if source_doc_id is not None:
        if kind != "binding" or _positive(source_doc_id) > 2147483647:
            raise ValueError("valid binding source required")
    params = _params(access_context, product_scope, site)
    doc_column = "ParentDocID" if kind == "occurrence" else "DocID"
    version_column = "ParentVersion" if kind == "occurrence" else "VersionNo"
    site_clause = "AND r.Site=:site" if kind != "declaration" else ""
    endpoint_clause = ""
    if kind == "binding":
        endpoint_clause = """AND EXISTS (SELECT 1 FROM EligibleDocuments source
            WHERE source.DocID=r.SourceDocID AND source.VersionNo=r.SourceVersion)"""
        if source_doc_id is not None:
            endpoint_clause += " AND r.SourceDocID=:source_doc_id"
    elif kind == "occurrence":
        endpoint_clause = """AND NOT EXISTS (
            SELECT 1 FROM dbo.ReviewedAssemblyBindings binding
            LEFT JOIN EligibleDocuments child ON child.DocID=binding.DocID AND child.VersionNo=binding.VersionNo
            LEFT JOIN EligibleDocuments source ON source.DocID=binding.SourceDocID AND source.VersionNo=binding.SourceVersion
            WHERE binding.PartKey=r.PartKey AND binding.ProductScope=r.ProductScope AND binding.Site=r.Site
              AND (child.DocID IS NULL OR source.DocID IS NULL))"""
    _ensure_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(_POLICY + f"""
            SELECT r.* FROM dbo.{table} r
            JOIN EligibleDocuments t ON t.DocID=r.{doc_column} AND t.VersionNo=r.{version_column}
            WHERE r.ProductScope=:product_scope AND r.ReviewStatus=:status
            {site_clause}
            {endpoint_clause}
            ORDER BY r.{id_column}
            OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY
        """), {**params, "source_doc_id":source_doc_id, "offset":offset,
                "status": status, "limit": max(1, min(int(limit), 500))}).mappings().all()
    return tuple(dict(row) for row in rows)


def review_graph_v2_record(kind, record_id, action, *, reviewer_id, access_context, product_scope, site, note):
    """Review one locked record using current evidence and the reviewer's ACL."""
    _positive(reviewer_id)
    note = _note(note)
    table, id_column, columns = _spec(kind)
    if action not in {"approve", "reject"}:
        raise ValueError("action must be approve or reject")
    if kind != "occurrence":
        _positive(record_id)
    elif not isinstance(record_id, str) or not record_id.strip():
        raise ValueError("occurrence id required")
    params = {**_params(access_context, product_scope, site), "record_id": record_id}
    _ensure_engine()
    with engine.begin() as conn:
        site_clause = " AND Site=:site" if kind != "declaration" else ""
        row = conn.execute(text(f"SELECT * FROM dbo.{table} WITH (UPDLOCK, ROWLOCK) "
                           f"WHERE {id_column}=:record_id AND ProductScope=:product_scope{site_clause}"), params).mappings().first()
        if not row:
            return {"ok": False, "reason": "not_found"}
        if row["ReviewStatus"] != "pending":
            return {"ok": False, "reason": "already_reviewed"}
        values = {key: row[column] for key, column in columns.items()}
        if kind == 'binding':
            values = {**values, 'source_hash':row['SourceHash'], 'snapshot_id':row['SnapshotID']}
        actor = _actor(conn, reviewer_id, kind, values)
        if not _proof(conn, kind, values, params):
            return {"ok": False, "reason": "invalid_provenance"}
        if action == "approve" and kind == "declaration" and values["rows_complete"]:
            actual = conn.execute(text(_ELIGIBLE_DOCUMENTS + """
                SELECT COUNT(*) FROM SafeOccurrences
                WHERE ParentDocID=:doc_id AND ParentVersion=:version_no
            """), {**params, **values}).scalar_one()
            if actual != values["expected_rows"]:
                return {"ok": False, "reason": "coverage_unverified"}
        status = "approved" if action == "approve" else "rejected"
        conn.execute(text(f"UPDATE dbo.{table} SET ReviewStatus=:status,ReviewedBy=:reviewer,"
                          f"ReviewedAt=SYSUTCDATETIME() WHERE {id_column}=:record_id AND ReviewStatus='pending'"),
                     {**params, "status": status, "reviewer": reviewer_id})
        _audit(conn, actor, kind, record_id, action, dict(row),
               {**values, "review_status": status, "reviewed_by": reviewer_id}, note)
    return {"ok": True, "record_id": record_id, "status": status}
