"""Persistence boundary for versioned graph community summary proposals."""

from __future__ import annotations

import json

from sqlalchemy import text

from mech_chatbot.rag.community_summaries import (
    build_pending_summary,
    evaluate_summary_serving,
)

from ..engine import _ensure_engine, engine


__all__ = [
    "list_community_summaries", "propose_community_summary",
    "review_community_summary",
]


def _serving_version(row, membership):
    return {
        "status": row["VersionStatus"],
        "prerequisite_graph_gate_passed": bool(
            row["PrerequisiteGraphGatePassed"]
        ),
        "structured_coverage": float(row["StructuredCoverage"] or 0),
        "reviewed_edge_precision": float(row["ReviewedEdgePrecision"] or 0),
        "min_global_answer_gain": float(row["MinGlobalAnswerGain"] or 0),
        "serving_epoch": row["ServingEpoch"],
        "detection_version": row["DetectionVersion"],
        "graph_fingerprint": row["GraphFingerprint"],
        "node_keys": sorted(membership),
    }


def propose_community_summary(community_version_id, **values):
    """Persist generated text as pending; this function cannot publish it."""
    proposal = build_pending_summary(**values)
    _ensure_engine()
    with engine.begin() as conn:
        row = conn.execute(text("""
            INSERT dbo.GraphCommunitySummary
                (CommunityVersionID, CommunityKey, SummaryText, SummarySha256,
                 NodeKeysJson, EdgeIDsJson, SourceProvenanceJson, Status, GeneratedBy)
            OUTPUT INSERTED.SummaryID, INSERTED.Status
            SELECT v.CommunityVersionID, :community_key, :summary_text, :summary_sha,
                   :nodes, :edges, :sources, 'pending', :generated_by
            FROM dbo.GraphCommunityVersion v
            WHERE v.CommunityVersionID=:version_id
              AND v.DetectionVersion=:detection_version
              AND v.ServingEpoch=:serving_epoch
              AND v.GraphFingerprint=:graph_fingerprint
              AND v.Status='approved'
              AND v.PrerequisiteGraphGatePassed=1
              AND v.StructuredCoverage >= 0.80000
              AND v.ReviewedEdgePrecision >= 0.95000
              AND v.MinGlobalAnswerGain > 0
        """), {
            "version_id": int(community_version_id),
            "community_key": proposal["community_key"],
            "summary_text": proposal["summary_text"],
            "summary_sha": proposal["summary_sha256"],
            "nodes": json.dumps(proposal["node_keys"], ensure_ascii=False),
            "edges": json.dumps(proposal["edge_ids"], ensure_ascii=False),
            "sources": json.dumps(proposal["source_provenance"], ensure_ascii=False),
            "generated_by": proposal["generated_by"],
            "detection_version": proposal["detection_version"],
            "serving_epoch": proposal["serving_epoch"],
            "graph_fingerprint": proposal["graph_fingerprint"],
        }).mappings().first()
    if not row:
        return {"ok": False, "reason": "community_version_mismatch"}
    return {
        "ok": True, "summary_id": int(row["SummaryID"]),
        "status": str(row["Status"]),
    }


def list_community_summaries(status="pending", limit=100):
    status = str(status or "pending").strip().lower()
    if status not in {"pending", "approved", "rejected", "disabled"}:
        raise ValueError("invalid community summary status")
    _ensure_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT TOP (:limit) s.SummaryID, s.CommunityVersionID,
                   s.CommunityKey, s.SummaryText, s.SummarySha256,
                   s.NodeKeysJson, s.EdgeIDsJson, s.SourceProvenanceJson,
                   s.Status, s.GeneratedBy, s.ReviewedBy, s.ReviewNote,
                   s.CreatedAt, s.ReviewedAt, v.DetectionVersion,
                   v.ServingEpoch, v.GraphFingerprint, v.Status AS VersionStatus
            FROM dbo.GraphCommunitySummary s
            JOIN dbo.GraphCommunityVersion v
              ON v.CommunityVersionID=s.CommunityVersionID
            WHERE s.Status=:status
            ORDER BY s.CreatedAt DESC
        """), {
            "limit": max(1, min(int(limit), 500)), "status": status,
        }).mappings().all()
    return [dict(row) for row in rows]


def review_community_summary(summary_id, action, reviewer, note=None):
    action = str(action or "").strip().lower()
    if action not in {"approve", "reject"}:
        raise ValueError("action must be approve or reject")
    target_status = "approved" if action == "approve" else "rejected"
    _ensure_engine()
    with engine.begin() as conn:
        row = conn.execute(text("""
            SELECT s.SummaryID, s.Status, s.CommunityVersionID,
                   s.CommunityKey, s.SummaryText,
                   s.NodeKeysJson, s.EdgeIDsJson, s.SourceProvenanceJson,
                   v.Status AS VersionStatus, v.DetectionVersion,
                   v.ServingEpoch, v.GraphFingerprint,
                   v.PrerequisiteGraphGatePassed, v.StructuredCoverage,
                   v.ReviewedEdgePrecision, v.MinGlobalAnswerGain
            FROM dbo.GraphCommunitySummary s WITH (UPDLOCK, ROWLOCK)
            JOIN dbo.GraphCommunityVersion v
              ON v.CommunityVersionID=s.CommunityVersionID
            WHERE s.SummaryID=:summary_id
        """), {"summary_id": int(summary_id)}).mappings().first()
        if not row:
            return {"ok": False, "reason": "not_found"}
        if row["Status"] != "pending":
            return {"ok": False, "reason": "already_reviewed", "status": row["Status"]}
        if action == "approve" and row["VersionStatus"] != "approved":
            return {"ok": False, "reason": "community_version_not_approved"}
        if action == "approve" and not (
            bool(row["PrerequisiteGraphGatePassed"])
            and float(row["StructuredCoverage"] or 0) >= 0.80
            and float(row["ReviewedEdgePrecision"] or 0) >= 0.95
            and float(row["MinGlobalAnswerGain"] or 0) > 0
        ):
            return {"ok": False, "reason": "community_prerequisite_not_met"}
        if action == "approve":
            try:
                node_keys = json.loads(row["NodeKeysJson"])
                edge_ids = json.loads(row["EdgeIDsJson"])
                sources = json.loads(row["SourceProvenanceJson"])
            except (TypeError, json.JSONDecodeError):
                return {"ok": False, "reason": "community_provenance_invalid"}
            edges = [dict(item) for item in conn.execute(text("""
                SELECT e.EdgeID edge_id, sn.CanonicalKey source_key,
                       tn.CanonicalKey target_key, e.ServingStatus serving_status,
                       e.SourceDocID doc_id, e.SourcePage page,
                       e.SourceVersion version, e.Department department,
                       e.Site site, e.SecurityLevel security_level
                FROM dbo.KnowledgeGraphEdge e
                JOIN dbo.KnowledgeGraphNode sn ON sn.NodeID=e.SourceNodeID
                JOIN dbo.KnowledgeGraphNode tn ON tn.NodeID=e.TargetNodeID
                WHERE e.EdgeID IN (
                    SELECT TRY_CONVERT(BIGINT, [value]) FROM OPENJSON(:edge_ids)
                )
            """), {"edge_ids": json.dumps(edge_ids)}).mappings().all()]
            documents = [dict(item) for item in conn.execute(text("""
                SELECT t.DocID doc_id, source.page, source.version,
                       t.ThuMuc department, t.Site site,
                       t.SecurityLevel security_level, t.Servable servable,
                       t.IsCurrent is_current,
                       t.PublicationState publication_state,
                       t.LifecycleStatus lifecycle_status,
                       t.ReviewStatus review_status
                FROM OPENJSON(:sources) WITH (
                    doc_id INT '$.doc_id', page INT '$.page',
                    version INT '$.version'
                ) source
                JOIN dbo.TaiLieu t ON t.DocID=source.doc_id
                                  AND t.VersionNo=source.version
            """), {"sources": json.dumps(sources)}).mappings().all()]
            membership = {
                str(item[0]) for item in conn.execute(text("""
                    SELECT n.CanonicalKey
                    FROM dbo.GraphCommunityMembership membership
                    JOIN dbo.KnowledgeGraphNode n ON n.NodeID=membership.NodeID
                    WHERE membership.CommunityVersionID=:version_id
                      AND membership.CommunityKey=:community_key
                """), {
                    "version_id": int(row["CommunityVersionID"]),
                    "community_key": row["CommunityKey"],
                }).all()
            }
            if membership != {str(value) for value in node_keys}:
                return {"ok": False, "reason": "community_membership_mismatch"}
            summary = {
                "status": "approved", "community_key": row["CommunityKey"],
                "summary_text": row["SummaryText"], "node_keys": node_keys,
                "edge_ids": edge_ids, "source_provenance": sources,
                "serving_epoch": row["ServingEpoch"],
                "detection_version": row["DetectionVersion"],
                "graph_fingerprint": row["GraphFingerprint"],
            }
            version = _serving_version(row, membership)
            decision = evaluate_summary_serving(
                summary, serving_epoch=row["ServingEpoch"],
                graph_fingerprint=row["GraphFingerprint"],
                access_context={"roles": ["admin"]},
                current_sources=documents, current_edges=edges,
                community_version=version,
            )
            if not decision.allowed:
                return {
                    "ok": False,
                    "reason": f"community_provenance_invalid:{decision.reason}",
                }
        conn.execute(text("""
            UPDATE dbo.GraphCommunitySummary
            SET Status=:status, ReviewedBy=:reviewer, ReviewNote=:note,
                ReviewedAt=SYSUTCDATETIME()
            WHERE SummaryID=:summary_id AND Status='pending'
        """), {
            "status": target_status,
            "reviewer": str(reviewer or "System")[:255],
            "note": str(note or "")[:1000] or None,
            "summary_id": int(summary_id),
        })
    return {
        "ok": True, "summary_id": int(summary_id), "status": target_status,
    }
