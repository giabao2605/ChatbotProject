"""Read-only SQL adapter for reviewed Graph v2 occurrence declarations."""

from __future__ import annotations

import json
from time import monotonic

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from ..engine import _ensure_engine, engine
from ..evidence_snapshot import evidence_read_connection
from mech_chatbot.rag.graph_retrieval_v2 import (
    MAX_RELATIONS, MAX_GENERATION_EDGES, traverse_graph_v2, traverse_where_used_v2, validate_graph_limits,
)


_CURRENT_DOCUMENTS = """
;WITH EligibleDocuments AS (
    SELECT t.* FROM dbo.TaiLieu t
    WHERE t.Servable=1 AND t.IsCurrent=1
      AND t.PublicationState='published' AND t.LifecycleStatus='published'
      AND t.ReviewStatus='approved'
      AND LOWER(ISNULL(t.EffectiveStatus,'effective')) NOT IN ('expired','superseded','draft')
      AND (t.EffectiveDate IS NULL OR t.EffectiveDate<=CAST(GETDATE() AS DATE))
      AND (t.ExpiryDate IS NULL OR t.ExpiryDate>=CAST(GETDATE() AS DATE))
      AND t.Site=:site
      AND (:is_admin=1 OR (
        EXISTS (SELECT 1 FROM dbo.PhongBanChiaSe s WHERE s.DocID=t.DocID
                AND (s.DeptCode='CHUNG' OR s.DeptCode IN (SELECT [value] FROM OPENJSON(:departments))))
        AND t.Site IN (SELECT [value] FROM OPENJSON(:sites))
        AND CASE LOWER(ISNULL(t.SecurityLevel,'confidential'))
              WHEN 'public' THEN 0 WHEN 'internal' THEN 1 ELSE 2 END<=:max_level
      ))
)
"""

_EDGE_BOM_QUOTE_PROOF = """
      AND EXISTS (
        SELECT 1 FROM dbo.BangKeVatTu edge_bom
        WHERE edge_bom.DocID=e.SourceDocID AND edge_bom.TrangSo=e.SourcePage
          AND 'part:'+LOWER(LTRIM(RTRIM(edge_bom.MaHang)))=tn.CanonicalKey
          AND e.SourceQuote=LEFT(NULLIF(LTRIM(RTRIM(edge_bom.RawRowJson)),N''),2000)
      )
"""


_ELIGIBLE_DOCUMENTS = _CURRENT_DOCUMENTS + """, SafeOccurrences AS (
    SELECT o.*, b.DocID AS ChildDocID, b.VersionNo AS ChildVersion,
           b.SourceDocID AS BindingSourceDocID, b.SourceVersion AS BindingSourceVersion,
           b.SourcePage AS BindingSourcePage, b.SourceQuote AS BindingSourceQuote,
           b.SourceHash AS BindingSourceHash,
           b.SnapshotID AS BindingSnapshotID
    FROM dbo.ReviewedGraphOccurrences o
    JOIN dbo.KnowledgeGraphEdge e ON e.EdgeID=o.EdgeID
    JOIN EligibleDocuments parent ON parent.DocID=o.ParentDocID
                                  AND parent.VersionNo=o.ParentVersion
    JOIN dbo.KnowledgeGraphNode sn ON sn.NodeID=e.SourceNodeID
    JOIN dbo.KnowledgeGraphNode tn ON tn.NodeID=e.TargetNodeID
    LEFT JOIN dbo.ReviewedAssemblyBindings b ON b.BindingID=o.ChildBindingID
    LEFT JOIN EligibleDocuments child ON child.DocID=b.DocID AND child.VersionNo=b.VersionNo
    LEFT JOIN EligibleDocuments binding_source ON binding_source.DocID=b.SourceDocID
                                                AND binding_source.VersionNo=b.SourceVersion
    LEFT JOIN dbo.ReviewedGraphDeclarations binding_declaration
      ON binding_declaration.DocID=b.SourceDocID
     AND binding_declaration.VersionNo=b.SourceVersion
     AND binding_declaration.SourceHash=b.SourceHash
     AND binding_declaration.SnapshotID=b.SnapshotID
     AND binding_declaration.ProductScope=o.ProductScope
     AND binding_declaration.ReviewStatus='approved'
    LEFT JOIN dbo.StructuredSourceSnapshot binding_snapshot
      ON binding_snapshot.DocID=binding_declaration.DocID
     AND binding_snapshot.VersionNo=binding_declaration.VersionNo
     AND binding_snapshot.SnapshotID=binding_declaration.SnapshotID
     AND binding_snapshot.SourceHash=binding_declaration.SourceHash
     AND binding_snapshot.CurrentSnapshot=1
    WHERE o.ProductScope=:product_scope AND o.Site=:site
      AND o.ReviewStatus='approved' AND o.ReviewedBy IS NOT NULL AND o.ReviewedAt IS NOT NULL
      AND e.ServingStatus='approved' AND e.RelationType='CONTAINS_PART'
      AND e.SourceDocID=o.ParentDocID AND e.SourceVersion=o.ParentVersion
      AND e.SourcePage=o.SourcePage AND o.SourcePage>0
      AND sn.CanonicalKey='document:'+CAST(o.ParentDocID AS NVARCHAR(30))
      AND tn.CanonicalKey=o.PartKey
      AND NULLIF(LTRIM(RTRIM(o.SourceQuote)),N'') IS NOT NULL
      AND EXISTS (
        SELECT 1 FROM dbo.StructuredNumericFact fact
        JOIN dbo.StructuredEvidenceScope scope ON scope.ScopeID=fact.ScopeID
        JOIN dbo.StructuredSourceSnapshot pinned ON pinned.DocID=fact.SourceDocID
          AND pinned.VersionNo=fact.SourceVersion AND pinned.SnapshotID=scope.SnapshotID
          AND pinned.SourceHash=fact.SourceHash AND pinned.CurrentSnapshot=1
        WHERE fact.SourceDocID=o.ParentDocID AND fact.SourceVersion=o.ParentVersion
          AND fact.ReviewStatus='approved' AND fact.ReviewedBy IS NOT NULL
          AND JSON_VALUE(fact.ReviewedPayloadJson,'$.locator.page')=CAST(o.SourcePage AS NVARCHAR(30))
          AND JSON_VALUE(fact.ReviewedPayloadJson,'$.locator.row_key')='page-'+CAST(o.SourcePage AS NVARCHAR(30))+':'+o.RowKey
          AND JSON_VALUE(fact.ReviewedPayloadJson,'$.value')=o.Quantity
          AND JSON_VALUE(fact.ReviewedPayloadJson,'$.unit')=o.Unit
      )
      AND EXISTS (
        SELECT 1 FROM dbo.BangKeVatTu bom
        WHERE bom.DocID=o.ParentDocID AND bom.TrangSo=o.SourcePage
          AND 'part:'+LOWER(LTRIM(RTRIM(bom.MaHang)))=o.PartKey
          AND JSON_VALUE(bom.RawRowJson,'$.source_row_id')=o.RowKey
          AND CHARINDEX(o.SourceQuote,COALESCE(bom.RawRowJson,N''))>0
      )""" + _EDGE_BOM_QUOTE_PROOF + """
      AND (o.ChildBindingID IS NULL OR (
        b.ReviewStatus='approved' AND b.ReviewedBy IS NOT NULL AND b.ReviewedAt IS NOT NULL
        AND b.ProductScope=o.ProductScope AND b.Site=o.Site AND b.PartKey=o.PartKey
        AND child.DocID IS NOT NULL AND binding_source.DocID IS NOT NULL
        AND binding_snapshot.SourceHash IS NOT NULL
        AND b.SourcePage>0 AND NULLIF(LTRIM(RTRIM(b.SourceQuote)),N'') IS NOT NULL
        AND (EXISTS (SELECT 1 FROM dbo.DocumentPages p
          WHERE p.DocID=b.SourceDocID AND p.PageNo=b.SourcePage
            AND (CHARINDEX(b.SourceQuote,COALESCE(p.TextExtract,N''))>0
              OR CHARINDEX(b.SourceQuote,COALESCE(p.LocalOCRText,N''))>0
              OR CHARINDEX(b.SourceQuote,COALESCE(p.VisionSummary,N''))>0))
          OR EXISTS (SELECT 1 FROM dbo.BangKeVatTu bom
            WHERE bom.DocID=b.SourceDocID AND bom.TrangSo=b.SourcePage
              AND CHARINDEX(b.SourceQuote,COALESCE(bom.RawRowJson,N''))>0))
      ))
      AND NOT EXISTS (
        SELECT 1 FROM dbo.ReviewedAssemblyBindings endpoint
        LEFT JOIN EligibleDocuments endpoint_doc ON endpoint_doc.DocID=endpoint.DocID
                                                   AND endpoint_doc.VersionNo=endpoint.VersionNo
        LEFT JOIN EligibleDocuments endpoint_source ON endpoint_source.DocID=endpoint.SourceDocID
                                                   AND endpoint_source.VersionNo=endpoint.SourceVersion
        WHERE endpoint.PartKey=o.PartKey AND endpoint.ProductScope=o.ProductScope
          AND endpoint.Site=o.Site AND endpoint.ReviewStatus='approved'
          AND (endpoint_doc.DocID IS NULL OR endpoint_source.DocID IS NULL)
      )
)
"""

_CURRENT_DECLARATION = """
FROM dbo.ReviewedGraphDeclarations d
JOIN EligibleDocuments t ON t.DocID=d.DocID AND t.VersionNo=d.VersionNo
JOIN dbo.StructuredSourceSnapshot snapshot ON snapshot.DocID=d.DocID
  AND snapshot.VersionNo=d.VersionNo AND snapshot.SnapshotID=d.SnapshotID
  AND snapshot.SourceHash=d.SourceHash AND snapshot.CurrentSnapshot=1
"""

_DECLARATION_PROOF = """
  AND d.ReviewStatus='approved' AND d.ReviewedBy IS NOT NULL AND d.ReviewedAt IS NOT NULL
  AND d.SourcePage>0 AND NULLIF(LTRIM(RTRIM(d.SourceQuote)),N'') IS NOT NULL
  AND (EXISTS (SELECT 1 FROM dbo.DocumentPages p
        WHERE p.DocID=d.DocID AND p.PageNo=d.SourcePage
          AND (CHARINDEX(d.SourceQuote,COALESCE(p.TextExtract,N''))>0
            OR CHARINDEX(d.SourceQuote,COALESCE(p.LocalOCRText,N''))>0
            OR CHARINDEX(d.SourceQuote,COALESCE(p.VisionSummary,N''))>0))
       OR EXISTS (SELECT 1 FROM dbo.BangKeVatTu bom
          WHERE bom.DocID=d.DocID AND bom.TrangSo=d.SourcePage
            AND CHARINDEX(d.SourceQuote,COALESCE(bom.RawRowJson,N''))>0))
"""

_ASSEMBLY_SQL = _ELIGIBLE_DOCUMENTS + """
SELECT d.DocID, d.VersionNo, d.SnapshotID, d.SourceHash, d.SourcePage,
       d.SourceQuote, d.RowsComplete, d.TreeComplete, d.ExpectedRows,
       o.OccurrenceID, o.PartKey, o.Quantity, o.Unit, o.QuantityBasis,
       o.IsLeaf, o.ChildDocID, o.ChildVersion, o.SourcePage AS RowSourcePage, o.SourceQuote AS RowSourceQuote,
       o.BindingSourceDocID, o.BindingSourceVersion, o.BindingSourcePage,
       o.BindingSourceQuote, o.BindingSourceHash, o.BindingSnapshotID
""" + _CURRENT_DECLARATION + """
OUTER APPLY (SELECT TOP (:remaining) * FROM SafeOccurrences rows
             WHERE rows.ParentDocID=d.DocID AND rows.ParentVersion=d.VersionNo
             ORDER BY rows.OccurrenceID) o
WHERE d.DocID=:doc_id AND d.ProductScope=:product_scope
""" + _DECLARATION_PROOF


def _source(row, *, binding=False, occurrence=False):
    if binding:
        return {"doc_id": row["BindingSourceDocID"], "version": row["BindingSourceVersion"],
                "page": row["BindingSourcePage"], "source_quote": row["BindingSourceQuote"],
                "source_hash": row["BindingSourceHash"], "snapshot_id": row["BindingSnapshotID"]}
    return {"doc_id": row["DocID"], "version": row["VersionNo"],
            "page": row["RowSourcePage" if occurrence else "SourcePage"],
            "source_quote": row["RowSourceQuote" if occurrence else "SourceQuote"],
            "source_hash": row["SourceHash"], "snapshot_id": row["SnapshotID"]}


class SqlGraphV2Reader:
    """One connection and one immutable access scope for the entire request."""

    def __init__(self, connection, *, product_scope, site, access_context):
        context = access_context or {}
        self.connection = connection
        self.params = {
            "product_scope": str(product_scope), "site": str(site),
            "departments": json.dumps(context.get("allowed_departments") or []),
            "sites": json.dumps(context.get("allowed_sites") or []),
            "is_admin": int("admin" in {str(role).lower() for role in context.get("roles", ())}),
            "max_level": {"public": 0, "internal": 1, "confidential": 2}.get(
                str(context.get("max_security_level") or "public").lower(), 0),
        }

    def assembly(self, doc_id, remaining):
        rows = self.connection.execute(text(_ASSEMBLY_SQL), {
            **self.params, "doc_id": int(doc_id),
            "remaining": max(1, min(int(remaining), MAX_RELATIONS)),
        }).mappings().all()
        if not rows:
            return {"available": False}
        first = rows[0]
        occurrences = []
        for row in rows:
            if row["OccurrenceID"] is None:
                continue
            sources = (_source(row, occurrence=True),)
            if row["BindingSourceDocID"] is not None:
                sources = (*sources, _source(row, binding=True))
            occurrences.append({
                "occurrence_id": str(row["OccurrenceID"]), "part_key": row["PartKey"],
                "quantity": row["Quantity"], "unit": row["Unit"],
                "quantity_basis": row["QuantityBasis"], "is_leaf": bool(row["IsLeaf"]),
                "child_doc_id": row["ChildDocID"], "child_version": row["ChildVersion"], "sources": sources,
                "relation_type": "CONTAINS_PART",
            })
        return {"available": True, "rows_complete": bool(first["RowsComplete"]),
                "tree_complete": bool(first["TreeComplete"]),
                "expected_rows": first["ExpectedRows"], "snapshot_id": first["SnapshotID"],
                "source": _source(first), "rows": tuple(occurrences)}

    def parents(self, key, remaining=MAX_RELATIONS):
        child_doc_id = int(key.split(':', 1)[1]) if str(key).startswith('document:') else None
        rows = self.connection.execute(text(_ELIGIBLE_DOCUMENTS + """
            SELECT DISTINCT TOP (:remaining) o.ParentDocID, o.ParentVersion,
                   o.OccurrenceID, o.PartKey, o.SourcePage, o.SourceQuote, d.SourceHash, d.SnapshotID,
                   o.BindingSourceDocID, o.BindingSourceVersion, o.BindingSourcePage,
                   o.BindingSourceQuote, o.BindingSourceHash, o.BindingSnapshotID
            FROM SafeOccurrences o
            JOIN dbo.ReviewedGraphDeclarations d ON d.DocID=o.ParentDocID
                AND d.VersionNo=o.ParentVersion AND d.ProductScope=o.ProductScope
            JOIN dbo.StructuredSourceSnapshot snapshot ON snapshot.DocID=d.DocID
                AND snapshot.VersionNo=d.VersionNo AND snapshot.SnapshotID=d.SnapshotID
                AND snapshot.SourceHash=d.SourceHash AND snapshot.CurrentSnapshot=1
            WHERE (o.PartKey=:part_key OR o.ChildDocID=:child_doc_id)
              AND d.ReviewStatus='approved'
              AND d.ReviewedBy IS NOT NULL AND d.ReviewedAt IS NOT NULL
            ORDER BY o.ParentDocID, o.OccurrenceID
        """), {**self.params, "part_key": None if child_doc_id is not None else str(key),
                "child_doc_id": child_doc_id, "remaining": max(1, min(int(remaining), MAX_RELATIONS))}).mappings().all()
        return tuple({"occurrence_id": str(row["OccurrenceID"]),
                      "source_key": f"document:{row['ParentDocID']}",
                      "target_key": row['PartKey'], "relation_type": "CONTAINS_PART",
                      "sources": ({"doc_id": row["ParentDocID"], "version": row["ParentVersion"],
                          "page": row["SourcePage"], "source_quote": row["SourceQuote"],
                          "source_hash": row["SourceHash"], "snapshot_id": row["SnapshotID"]},)
                          + ((_source(row, binding=True),) if row['BindingSourceDocID'] is not None else ())}
                     for row in rows)


def list_approved_graph_scopes(doc_id, profile, *, site, offset=0, limit=50):
    """Page only readable declarations; this does not certify a complete tree."""
    if (type(doc_id) is not int or not 1 <= doc_id <= 2147483647
            or type(offset) is not int or not 0 <= offset <= 100000
            or type(limit) is not int or not 1 <= limit <= 50
            or not isinstance(site, str) or not site.strip() or len(site) > 100):
        raise ValueError('Invalid graph scope selection request')
    _ensure_engine()
    with evidence_read_connection(engine) as conn:
        reader = SqlGraphV2Reader(conn, product_scope='', site=site, access_context=profile)
        query = (_CURRENT_DOCUMENTS + 'SELECT d.ProductScope AS product_scope, t.Site AS site\n'
                 + _CURRENT_DECLARATION + 'WHERE d.DocID=:doc_id\n' + _DECLARATION_PROOF
                 + 'ORDER BY d.ProductScope OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY')
        rows = conn.execute(text(query), {**reader.params, 'doc_id':doc_id,
                                         'offset':offset, 'limit':limit+1}).mappings().all()
    return {'items':[dict(row) for row in rows[:limit]], 'offset':offset, 'limit':limit,
            'has_more':len(rows) > limit}


def retrieve_graph_v2(root_key, access_context, *, product_scope, site,
                      direction="contains", deadline=None, max_relations=MAX_RELATIONS, max_generation_edges=MAX_GENERATION_EDGES):
    """Serve the SQL-backed experimental contract without granting activation."""
    if direction not in {"contains", "where_used"}:
        raise ValueError("direction must be contains or where_used")
    if not str(product_scope).strip() or not str(site).strip():
        raise ValueError("product scope and site required")
    if direction == "contains":
        if not str(root_key).startswith("document:"):
            raise ValueError("contains root must be an explicit document key")
        root_doc_id = int(str(root_key).split(":", 1)[1])
        if root_doc_id <= 0:
            raise ValueError("positive document required")
    elif not str(root_key).startswith("part:") or not str(root_key)[5:].strip():
        raise ValueError("where_used root must be an explicit part key")
    if deadline is not None and monotonic() >= deadline:
        return {"contract_version": "graph-v2", "status": "unknown",
                "reasons": ("deadline",), "edges": (), "manifest": None}
    validate_graph_limits(max_relations,max_generation_edges)
    if max_relations==0:
        return {"contract_version":"graph-v2","status":"unknown","reasons":("relation_limit",),"edges":(),"manifest":None,"examined_relations":0}
    _ensure_engine()
    try:
        with evidence_read_connection(engine) as conn:
            reader = SqlGraphV2Reader(conn, product_scope=product_scope, site=site,
                                      access_context=access_context)
            if direction == "contains":
                return traverse_graph_v2(root_doc_id, reader, product_scope=product_scope,
                                         deadline=deadline,max_relations=max_relations,max_generation_edges=max_generation_edges)
            return traverse_where_used_v2(str(root_key).lower(), reader, deadline=deadline,max_relations=max_relations,max_generation_edges=max_generation_edges)
    except SQLAlchemyError:
        return {"contract_version": "graph-v2", "status": "unknown",
                "reasons": ("storage_unavailable",), "edges": (), "manifest": None}


def graph_query_in_connection(conn,query,profile,*,deadline=None):
    """Revalidate a persisted graph with the history read's existing connection."""
    reader=SqlGraphV2Reader(conn,product_scope=query.product_scope,site=query.site,access_context=profile)
    if query.direction=='contains':
        return traverse_graph_v2(int(query.root_key[9:]),reader,product_scope=query.product_scope,deadline=deadline,max_relations=query.max_relations,max_generation_edges=query.max_generation_edges)
    return traverse_where_used_v2(query.root_key.lower(),reader,deadline=deadline,max_relations=query.max_relations,max_generation_edges=query.max_generation_edges)
