"""SQL Server persistence for governed graph proposals and serving edges."""

from __future__ import annotations

import json
import re

from sqlalchemy import text

from ..engine import _ensure_engine, engine


__all__ = [
    "list_graph_proposals", "propose_graph_edge", "review_graph_proposal",
    "traverse_knowledge_graph",
]


def propose_graph_edge(
    source_node_id, target_node_id, relation_type, *, doc_id, page, version,
    confidence=None, evidence=None, proposed_by="graph-extractor",
):
    """Persist an extracted edge as pending; this path never creates a serving edge."""
    relation = str(relation_type or "").strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,99}", relation):
        raise ValueError("invalid graph relation type")
    safe_evidence = {
        str(key): value for key, value in dict(evidence or {}).items()
        if str(key).strip().lower() not in {"prompt", "raw_prompt", "question"}
    }
    _ensure_engine()
    with engine.begin() as conn:
        row = conn.execute(text("""
            INSERT dbo.GraphExtractionProposal
                (SourceNodeID, TargetNodeID, RelationType, SourceDocID, SourcePage,
                 SourceVersion, Confidence, EvidenceJson, Status, ProposedBy)
            OUTPUT INSERTED.ProposalID, INSERTED.Status
            SELECT :source_node, :target_node, :relation, t.DocID, :page,
                   :version, :confidence, :evidence, 'pending', :proposed_by
            FROM dbo.TaiLieu t
            WHERE t.DocID=:doc_id
              AND t.VersionNo=:version
              AND :page > 0
              AND EXISTS (SELECT 1 FROM dbo.KnowledgeGraphNode WHERE NodeID=:source_node)
              AND EXISTS (SELECT 1 FROM dbo.KnowledgeGraphNode WHERE NodeID=:target_node)
        """), {
            "source_node": int(source_node_id), "target_node": int(target_node_id),
            "relation": relation, "doc_id": int(doc_id), "page": int(page),
            "version": int(version), "confidence": confidence,
            "evidence": json.dumps(safe_evidence, ensure_ascii=False),
            "proposed_by": str(proposed_by or "graph-extractor")[:255],
        }).mappings().first()
    if not row:
        return {"ok": False, "reason": "invalid_provenance"}
    return {"ok": True, "proposal_id": int(row["ProposalID"]), "status": row["Status"]}


def list_graph_proposals(status="pending", limit=100):
    _ensure_engine()
    allowed = {"pending", "approved", "rejected"}
    status = str(status or "pending").lower()
    if status not in allowed:
        raise ValueError("invalid graph proposal status")
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT TOP (:limit) p.ProposalID, p.SourceNodeID, sn.DisplayName,
                   p.TargetNodeID, tn.DisplayName, p.RelationType, p.SourceDocID,
                   p.SourcePage, p.SourceVersion, p.Confidence, p.EvidenceJson,
                   p.Status, p.ProposedBy, p.CreatedAt, p.ReviewedBy, p.ReviewNote
            FROM dbo.GraphExtractionProposal p
            JOIN dbo.KnowledgeGraphNode sn ON sn.NodeID = p.SourceNodeID
            JOIN dbo.KnowledgeGraphNode tn ON tn.NodeID = p.TargetNodeID
            WHERE p.Status = :status
            ORDER BY p.CreatedAt DESC
        """), {"limit": max(1, min(int(limit), 500)), "status": status}).mappings().all()
    return [dict(row) for row in rows]


def review_graph_proposal(proposal_id, action, reviewer, note=None):
    _ensure_engine()
    action = str(action or "").lower()
    if action not in {"approve", "reject"}:
        raise ValueError("action must be approve or reject")
    target_status = "approved" if action == "approve" else "rejected"
    with engine.begin() as conn:
        proposal = conn.execute(text("""
            SELECT p.ProposalID, p.SourceNodeID, p.TargetNodeID, p.RelationType,
                   p.SourceDocID, p.SourcePage, p.SourceVersion, p.Confidence,
                   p.Status, t.ThuMuc, t.Site, t.SecurityLevel
            FROM dbo.GraphExtractionProposal p WITH (UPDLOCK, ROWLOCK)
            JOIN dbo.TaiLieu t ON t.DocID = p.SourceDocID AND t.VersionNo = p.SourceVersion
            WHERE p.ProposalID = :proposal_id
              AND p.SourcePage > 0
              AND t.Servable=1 AND t.IsCurrent=1
              AND t.PublicationState='published' AND t.LifecycleStatus='published'
              AND t.ReviewStatus='approved'
        """), {"proposal_id": int(proposal_id)}).mappings().first()
        if not proposal:
            return {"ok": False, "reason": "not_found"}
        if proposal["Status"] != "pending":
            return {"ok": False, "reason": "already_reviewed", "status": proposal["Status"]}
        if action == "approve":
            conn.execute(text("""
                MERGE dbo.KnowledgeGraphEdge AS target
                USING (SELECT :source_node AS SourceNodeID, :target_node AS TargetNodeID,
                              :relation AS RelationType, :doc_id AS SourceDocID,
                              :page AS SourcePage) AS source
                ON target.SourceNodeID = source.SourceNodeID
                   AND target.TargetNodeID = source.TargetNodeID
                   AND target.RelationType = source.RelationType
                   AND target.SourceDocID = source.SourceDocID
                   AND target.SourcePage = source.SourcePage
                WHEN MATCHED THEN UPDATE SET SourceVersion=:version, Confidence=:confidence,
                    Department=:department, Site=:site, SecurityLevel=:security,
                    Origin='llm', ServingStatus='approved', ReviewedBy=:reviewer,
                    ReviewedAt=SYSUTCDATETIME()
                WHEN NOT MATCHED THEN INSERT
                    (SourceNodeID, TargetNodeID, RelationType, Origin, ServingStatus,
                     Confidence, SourceDocID, SourcePage, SourceVersion, Department,
                     Site, SecurityLevel, ReviewedBy, ReviewedAt)
                VALUES (:source_node, :target_node, :relation, 'llm', 'approved',
                        :confidence, :doc_id, :page, :version, :department, :site,
                        :security, :reviewer, SYSUTCDATETIME());
            """), {
                "source_node": proposal["SourceNodeID"], "target_node": proposal["TargetNodeID"],
                "relation": proposal["RelationType"], "confidence": proposal["Confidence"],
                "doc_id": proposal["SourceDocID"], "page": proposal["SourcePage"],
                "version": proposal["SourceVersion"], "department": proposal["ThuMuc"],
                "site": proposal["Site"], "security": proposal["SecurityLevel"] or "confidential",
                "reviewer": str(reviewer or "System")[:255],
            })
        conn.execute(text("""
            UPDATE dbo.GraphExtractionProposal
            SET Status = :status, ReviewedBy = :reviewer, ReviewNote = :note,
                ReviewedAt = SYSUTCDATETIME()
            WHERE ProposalID = :proposal_id AND Status = 'pending'
        """), {
            "status": target_status, "reviewer": str(reviewer or "System")[:255],
            "note": str(note or "")[:1000] or None, "proposal_id": int(proposal_id),
        })
    return {"ok": True, "proposal_id": int(proposal_id), "status": target_status}


def traverse_knowledge_graph(seed_keys, access_context, max_hops=2, limit=50):
    """Traverse approved outgoing edges while enforcing document governance in SQL."""
    from mech_chatbot.rag.graph_retrieval import expand_seed_keys

    keys = expand_seed_keys(seed_keys)
    if not keys:
        return []
    context = access_context or {}
    roles = {str(item).strip().lower() for item in context.get("roles", ())}
    params = {
        "keys": json.dumps(keys),
        "departments": json.dumps(context.get("allowed_departments") or []),
        "sites": json.dumps(context.get("allowed_sites") or []),
        "max_level": {"public": 0, "internal": 1, "confidential": 2}.get(
            str(context.get("max_security_level") or "public").lower(), 0
        ),
        "is_admin": 1 if "admin" in roles else 0,
        "max_hops": max(1, min(int(max_hops), 2)),
        "limit": max(1, min(int(limit), 50)),
    }
    _ensure_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            ;WITH EligibleEdges AS (
                SELECT e.*
                FROM dbo.KnowledgeGraphEdge e
                JOIN dbo.TaiLieu governed ON governed.DocID = e.SourceDocID
                WHERE e.ServingStatus = 'approved'
                  AND governed.Servable = 1 AND governed.IsCurrent = 1
                  AND governed.PublicationState = 'published'
                  AND governed.LifecycleStatus = 'published'
                  AND governed.ReviewStatus = 'approved'
                  AND e.SourceVersion = governed.VersionNo
                  AND e.SourcePage > 0
                  AND LOWER(ISNULL(governed.EffectiveStatus, 'effective')) NOT IN ('expired','superseded','draft')
                  AND (governed.EffectiveDate IS NULL OR governed.EffectiveDate <= CAST(GETDATE() AS DATE))
                  AND (governed.ExpiryDate IS NULL OR governed.ExpiryDate >= CAST(GETDATE() AS DATE))
                  AND (:is_admin = 1 OR (
                        (governed.ThuMuc IN (SELECT [value] FROM OPENJSON(:departments))
                         OR EXISTS (SELECT 1 FROM dbo.PhongBanChiaSe shared
                                    WHERE shared.DocID = governed.DocID
                                      AND shared.DeptCode IN (SELECT [value] FROM OPENJSON(:departments))))
                        AND governed.Site IN (SELECT [value] FROM OPENJSON(:sites))
                        AND CASE LOWER(ISNULL(governed.SecurityLevel, 'confidential'))
                              WHEN 'public' THEN 0 WHEN 'internal' THEN 1 ELSE 2 END <= :max_level
                  ))
            ),
            Walk AS (
                SELECT n.NodeID, CAST(0 AS INT) AS Depth,
                       CAST('/' + CAST(n.NodeID AS VARCHAR(30)) + '/' AS VARCHAR(MAX)) AS Path
                FROM dbo.KnowledgeGraphNode n
                WHERE LOWER(n.CanonicalKey) IN (SELECT [value] FROM OPENJSON(:keys))
                   OR LOWER(ISNULL(n.DisplayName, '')) IN (SELECT [value] FROM OPENJSON(:keys))
                UNION ALL
                SELECT e.TargetNodeID, w.Depth + 1,
                       CAST(w.Path + CAST(e.TargetNodeID AS VARCHAR(30)) + '/' AS VARCHAR(MAX))
                FROM Walk w
                JOIN EligibleEdges e ON e.SourceNodeID = w.NodeID
                WHERE w.Depth < :max_hops
                  AND w.Path NOT LIKE '%/' + CAST(e.TargetNodeID AS VARCHAR(30)) + '/%'
            )
            SELECT DISTINCT TOP (:limit) e.EdgeID AS edge_id, e.RelationType AS relation_type,
                   sn.CanonicalKey AS source_key, sn.DisplayName AS source_name,
                   tn.CanonicalKey AS target_key, tn.DisplayName AS target_name,
                   e.Origin AS origin, e.ServingStatus AS serving_status,
                   e.SourceDocID AS doc_id, e.SourcePage AS page, e.SourceVersion AS version,
                   t.TenFile AS file_goc, t.ThuMuc AS department, t.Site AS site,
                   t.SecurityLevel AS security_level, t.Servable AS servable,
                   t.IsCurrent AS is_current, t.PublicationState AS publication_state,
                   t.LifecycleStatus AS lifecycle_status, t.ReviewStatus AS review_status
            FROM Walk w
            JOIN EligibleEdges e ON e.SourceNodeID = w.NodeID
            JOIN dbo.KnowledgeGraphNode sn ON sn.NodeID = e.SourceNodeID
            JOIN dbo.KnowledgeGraphNode tn ON tn.NodeID = e.TargetNodeID
            JOIN dbo.TaiLieu t ON t.DocID = e.SourceDocID
            WHERE w.Depth < :max_hops
            OPTION (MAXRECURSION 2)
        """), params).mappings().all()
    return [dict(row) for row in rows]
