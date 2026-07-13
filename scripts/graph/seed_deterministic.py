"""Idempotently seed reviewed graph facts from SQL-owned document metadata."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import text


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for item in (ROOT, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from mech_chatbot.db.engine import _ensure_engine  # noqa: E402


DEFAULT_DEPARTMENTS = ("Technical", "Production", "Maintenance")


def seed(departments=DEFAULT_DEPARTMENTS):
    import mech_chatbot.db.engine as db_engine

    _ensure_engine()
    params = {"departments": json.dumps(list(departments))}
    statements = [
        """
        MERGE dbo.KnowledgeGraphNode AS target
        USING (
            SELECT 'document_family' AS NodeType,
                   'family:' + CAST(f.FamilyID AS NVARCHAR(30)) AS CanonicalKey,
                   COALESCE(f.FamilyName, f.BaseCode) AS DisplayName,
                   current_doc.DocID AS SourceDocID, NULL AS SourcePage,
                   current_doc.VersionNo AS SourceVersion, current_doc.ThuMuc AS Department,
                   current_doc.Site, ISNULL(current_doc.SecurityLevel, 'confidential') AS SecurityLevel
            FROM dbo.DocumentFamily f
            CROSS APPLY (
                SELECT TOP 1 t.DocID, t.VersionNo, t.ThuMuc, t.Site, t.SecurityLevel
                FROM dbo.TaiLieu t
                WHERE t.FamilyID=f.FamilyID
                  AND t.ThuMuc IN (SELECT [value] FROM OPENJSON(:departments))
                  AND t.ReviewStatus='approved' AND t.PublicationState='published'
                ORDER BY t.IsCurrent DESC, t.VersionNo DESC, t.DocID DESC
            ) current_doc
        ) source ON target.NodeType=source.NodeType AND target.CanonicalKey=source.CanonicalKey
        WHEN MATCHED THEN UPDATE SET DisplayName=source.DisplayName, SourceDocID=source.SourceDocID,
             SourceVersion=source.SourceVersion, Department=source.Department, Site=source.Site,
             SecurityLevel=source.SecurityLevel
        WHEN NOT MATCHED THEN INSERT
             (NodeType,CanonicalKey,DisplayName,SourceDocID,SourcePage,SourceVersion,Department,Site,SecurityLevel)
             VALUES(source.NodeType,source.CanonicalKey,source.DisplayName,source.SourceDocID,
                    source.SourcePage,source.SourceVersion,source.Department,source.Site,source.SecurityLevel);
        """,
        """
        MERGE dbo.KnowledgeGraphNode AS target
        USING (
            SELECT 'document' AS NodeType, 'document:' + CAST(t.DocID AS NVARCHAR(30)) AS CanonicalKey,
                   COALESCE(t.BaseCode, t.TenFile) AS DisplayName, t.DocID AS SourceDocID,
                   NULL AS SourcePage, t.VersionNo AS SourceVersion, t.ThuMuc AS Department,
                   t.Site, ISNULL(t.SecurityLevel, 'confidential') AS SecurityLevel
            FROM dbo.TaiLieu t
            WHERE t.ThuMuc IN (SELECT [value] FROM OPENJSON(:departments))
              AND t.ReviewStatus = 'approved' AND t.PublicationState = 'published'
        ) source ON target.NodeType = source.NodeType AND target.CanonicalKey = source.CanonicalKey
        WHEN MATCHED THEN UPDATE SET DisplayName=source.DisplayName, SourceDocID=source.SourceDocID,
             SourceVersion=source.SourceVersion, Department=source.Department, Site=source.Site,
             SecurityLevel=source.SecurityLevel
        WHEN NOT MATCHED THEN INSERT
             (NodeType, CanonicalKey, DisplayName, SourceDocID, SourcePage, SourceVersion, Department, Site, SecurityLevel)
             VALUES (source.NodeType, source.CanonicalKey, source.DisplayName, source.SourceDocID,
                     source.SourcePage, source.SourceVersion, source.Department, source.Site, source.SecurityLevel);
        """,
        """
        MERGE dbo.KnowledgeGraphNode AS target
        USING (
            SELECT 'page' AS NodeType,
                   'page:' + CAST(p.DocID AS NVARCHAR(30)) + ':' + CAST(p.PageNo AS NVARCHAR(30)) AS CanonicalKey,
                   p.FileName + ' page ' + CAST(p.PageNo AS NVARCHAR(30)) AS DisplayName,
                   p.DocID AS SourceDocID, p.PageNo AS SourcePage, t.VersionNo AS SourceVersion,
                   t.ThuMuc AS Department, t.Site, ISNULL(t.SecurityLevel, 'confidential') AS SecurityLevel
            FROM dbo.DocumentPages p JOIN dbo.TaiLieu t ON t.DocID=p.DocID
            WHERE t.ThuMuc IN (SELECT [value] FROM OPENJSON(:departments))
              AND t.Servable=1 AND t.IsCurrent=1 AND t.ReviewStatus='approved'
              AND t.PublicationState='published' AND t.LifecycleStatus='published'
        ) source ON target.NodeType=source.NodeType AND target.CanonicalKey=source.CanonicalKey
        WHEN NOT MATCHED THEN INSERT
             (NodeType, CanonicalKey, DisplayName, SourceDocID, SourcePage, SourceVersion, Department, Site, SecurityLevel)
             VALUES (source.NodeType, source.CanonicalKey, source.DisplayName, source.SourceDocID,
                     source.SourcePage, source.SourceVersion, source.Department, source.Site, source.SecurityLevel);
        """,
        """
        MERGE dbo.KnowledgeGraphNode AS target
        USING (
            SELECT NodeType, CanonicalKey, DisplayName, SourceDocID, SourcePage,
                   SourceVersion, Department, Site, SecurityLevel
            FROM (
                SELECT 'part' AS NodeType, 'part:' + LOWER(LTRIM(RTRIM(b.MaHang))) AS CanonicalKey,
                       COALESCE(NULLIF(LTRIM(RTRIM(b.TenVatTu)), ''), b.MaHang) AS DisplayName,
                       b.DocID AS SourceDocID, b.TrangSo AS SourcePage, t.VersionNo AS SourceVersion,
                       t.ThuMuc AS Department, t.Site, ISNULL(t.SecurityLevel, 'confidential') AS SecurityLevel,
                       ROW_NUMBER() OVER (PARTITION BY LOWER(LTRIM(RTRIM(b.MaHang)))
                                          ORDER BY t.VersionNo DESC, b.DocID DESC, b.ID DESC) AS rn
                FROM dbo.BangKeVatTu b JOIN dbo.TaiLieu t ON t.DocID=b.DocID
                WHERE b.MaHang IS NOT NULL AND LTRIM(RTRIM(b.MaHang)) <> ''
                  AND t.ThuMuc IN (SELECT [value] FROM OPENJSON(:departments))
                  AND t.Servable=1 AND t.IsCurrent=1 AND t.ReviewStatus='approved'
                  AND t.PublicationState='published' AND t.LifecycleStatus='published'
            ) ranked WHERE rn=1
        ) source ON target.NodeType=source.NodeType AND target.CanonicalKey=source.CanonicalKey
        WHEN NOT MATCHED THEN INSERT
             (NodeType, CanonicalKey, DisplayName, SourceDocID, SourcePage, SourceVersion, Department, Site, SecurityLevel)
             VALUES (source.NodeType, source.CanonicalKey, source.DisplayName, source.SourceDocID,
                     source.SourcePage, source.SourceVersion, source.Department, source.Site, source.SecurityLevel);
        """,
        """
        MERGE dbo.KnowledgeGraphNode AS target
        USING (
            SELECT NodeType, CanonicalKey, DisplayName, SourceDocID, SourcePage,
                   SourceVersion, Department, Site, SecurityLevel
            FROM (
                SELECT 'material' AS NodeType,
                       'material:' + LOWER(LTRIM(RTRIM(COALESCE(NULLIF(b.NormalizedMaterial,''), b.VatLieu)))) AS CanonicalKey,
                       COALESCE(NULLIF(b.NormalizedMaterial,''), b.VatLieu) AS DisplayName,
                       b.DocID AS SourceDocID, b.TrangSo AS SourcePage, t.VersionNo AS SourceVersion,
                       t.ThuMuc AS Department, t.Site, ISNULL(t.SecurityLevel, 'confidential') AS SecurityLevel,
                       ROW_NUMBER() OVER (
                           PARTITION BY LOWER(LTRIM(RTRIM(COALESCE(NULLIF(b.NormalizedMaterial,''), b.VatLieu))))
                           ORDER BY t.VersionNo DESC, b.DocID DESC, b.ID DESC
                       ) AS rn
                FROM dbo.BangKeVatTu b JOIN dbo.TaiLieu t ON t.DocID=b.DocID
                WHERE COALESCE(NULLIF(b.NormalizedMaterial,''), b.VatLieu) IS NOT NULL
                  AND t.ThuMuc IN (SELECT [value] FROM OPENJSON(:departments))
                  AND t.Servable=1 AND t.IsCurrent=1 AND t.ReviewStatus='approved'
                  AND t.PublicationState='published' AND t.LifecycleStatus='published'
            ) ranked WHERE rn=1
        ) source ON target.NodeType=source.NodeType AND target.CanonicalKey=source.CanonicalKey
        WHEN NOT MATCHED THEN INSERT
             (NodeType, CanonicalKey, DisplayName, SourceDocID, SourcePage, SourceVersion, Department, Site, SecurityLevel)
             VALUES (source.NodeType, source.CanonicalKey, source.DisplayName, source.SourceDocID,
                     source.SourcePage, source.SourceVersion, source.Department, source.Site, source.SecurityLevel);
        """,
        """
        MERGE dbo.KnowledgeGraphEdge AS target
        USING (
            SELECT fn.NodeID SourceNodeID, dn.NodeID TargetNodeID, 'HAS_VERSION' RelationType,
                   t.DocID SourceDocID, COALESCE(page_ref.PageNo, bom_ref.PageNo, 1) SourcePage, t.VersionNo SourceVersion,
                   t.ThuMuc Department, t.Site, ISNULL(t.SecurityLevel,'confidential') SecurityLevel
            FROM dbo.TaiLieu t
            JOIN dbo.KnowledgeGraphNode fn ON fn.CanonicalKey='family:'+CAST(t.FamilyID AS NVARCHAR(30))
            JOIN dbo.KnowledgeGraphNode dn ON dn.CanonicalKey='document:'+CAST(t.DocID AS NVARCHAR(30))
            OUTER APPLY (SELECT TOP 1 PageNo FROM dbo.DocumentPages WHERE DocID=t.DocID ORDER BY PageNo) page_ref
            OUTER APPLY (SELECT MIN(TrangSo) PageNo FROM dbo.BangKeVatTu WHERE DocID=t.DocID) bom_ref
            WHERE t.FamilyID IS NOT NULL
              AND t.ThuMuc IN (SELECT [value] FROM OPENJSON(:departments))
              AND t.ReviewStatus='approved' AND t.PublicationState='published'
        ) source ON target.SourceNodeID=source.SourceNodeID AND target.TargetNodeID=source.TargetNodeID
                   AND target.RelationType=source.RelationType AND target.SourceDocID=source.SourceDocID
                   AND target.SourcePage=source.SourcePage
        WHEN NOT MATCHED THEN INSERT
             (SourceNodeID,TargetNodeID,RelationType,Origin,ServingStatus,Confidence,SourceDocID,SourcePage,
              SourceVersion,Department,Site,SecurityLevel,ReviewedBy,ReviewedAt)
             VALUES(source.SourceNodeID,source.TargetNodeID,source.RelationType,'deterministic','approved',1,
                    source.SourceDocID,source.SourcePage,source.SourceVersion,source.Department,source.Site,
                    source.SecurityLevel,'deterministic-seed',SYSUTCDATETIME());
        """,
        """
        MERGE dbo.KnowledgeGraphEdge AS target
        USING (
            SELECT current_node.NodeID SourceNodeID, old_node.NodeID TargetNodeID, 'SUPERSEDES' RelationType,
                   t.DocID SourceDocID, COALESCE(page_ref.PageNo, bom_ref.PageNo, 1) SourcePage, t.VersionNo SourceVersion,
                   t.ThuMuc Department, t.Site, ISNULL(t.SecurityLevel,'confidential') SecurityLevel
            FROM dbo.TaiLieu t
            JOIN dbo.KnowledgeGraphNode current_node ON current_node.CanonicalKey='document:'+CAST(t.DocID AS NVARCHAR(30))
            JOIN dbo.KnowledgeGraphNode old_node ON old_node.CanonicalKey='document:'+CAST(t.SupersedesDocID AS NVARCHAR(30))
            OUTER APPLY (SELECT TOP 1 PageNo FROM dbo.DocumentPages WHERE DocID=t.DocID ORDER BY PageNo) page_ref
            OUTER APPLY (SELECT MIN(TrangSo) PageNo FROM dbo.BangKeVatTu WHERE DocID=t.DocID) bom_ref
            WHERE t.SupersedesDocID IS NOT NULL
              AND t.ThuMuc IN (SELECT [value] FROM OPENJSON(:departments))
              AND t.ReviewStatus='approved' AND t.PublicationState='published'
        ) source ON target.SourceNodeID=source.SourceNodeID AND target.TargetNodeID=source.TargetNodeID
                   AND target.RelationType=source.RelationType AND target.SourceDocID=source.SourceDocID
                   AND target.SourcePage=source.SourcePage
        WHEN NOT MATCHED THEN INSERT
             (SourceNodeID,TargetNodeID,RelationType,Origin,ServingStatus,Confidence,SourceDocID,SourcePage,
              SourceVersion,Department,Site,SecurityLevel,ReviewedBy,ReviewedAt)
             VALUES(source.SourceNodeID,source.TargetNodeID,source.RelationType,'deterministic','approved',1,
                    source.SourceDocID,source.SourcePage,source.SourceVersion,source.Department,source.Site,
                    source.SecurityLevel,'deterministic-seed',SYSUTCDATETIME());
        """,
        """
        MERGE dbo.KnowledgeGraphEdge AS target
        USING (
            SELECT dn.NodeID SourceNodeID, pn.NodeID TargetNodeID, 'HAS_PAGE' RelationType,
                   p.DocID SourceDocID, p.PageNo SourcePage, t.VersionNo SourceVersion,
                   t.ThuMuc Department, t.Site, ISNULL(t.SecurityLevel,'confidential') SecurityLevel
            FROM dbo.DocumentPages p JOIN dbo.TaiLieu t ON t.DocID=p.DocID
            JOIN dbo.KnowledgeGraphNode dn ON dn.CanonicalKey='document:'+CAST(p.DocID AS NVARCHAR(30))
            JOIN dbo.KnowledgeGraphNode pn ON pn.CanonicalKey='page:'+CAST(p.DocID AS NVARCHAR(30))+':'+CAST(p.PageNo AS NVARCHAR(30))
            WHERE t.ThuMuc IN (SELECT [value] FROM OPENJSON(:departments)) AND t.Servable=1 AND t.IsCurrent=1
        ) source ON target.SourceNodeID=source.SourceNodeID AND target.TargetNodeID=source.TargetNodeID
                   AND target.RelationType=source.RelationType AND target.SourceDocID=source.SourceDocID
                   AND target.SourcePage=source.SourcePage
        WHEN NOT MATCHED THEN INSERT
             (SourceNodeID,TargetNodeID,RelationType,Origin,ServingStatus,Confidence,SourceDocID,SourcePage,
              SourceVersion,Department,Site,SecurityLevel,ReviewedBy,ReviewedAt)
             VALUES(source.SourceNodeID,source.TargetNodeID,source.RelationType,'deterministic','approved',1,
                    source.SourceDocID,source.SourcePage,source.SourceVersion,source.Department,source.Site,
                    source.SecurityLevel,'deterministic-seed',SYSUTCDATETIME());
        """,
        """
        MERGE dbo.KnowledgeGraphEdge AS target
        USING (
            SELECT dn.NodeID SourceNodeID, pn.NodeID TargetNodeID, 'CONTAINS_PART' RelationType,
                   b.DocID SourceDocID, b.TrangSo SourcePage, t.VersionNo SourceVersion,
                   t.ThuMuc Department, t.Site, ISNULL(t.SecurityLevel,'confidential') SecurityLevel
            FROM dbo.BangKeVatTu b JOIN dbo.TaiLieu t ON t.DocID=b.DocID
            JOIN dbo.KnowledgeGraphNode dn ON dn.CanonicalKey='document:'+CAST(b.DocID AS NVARCHAR(30))
            JOIN dbo.KnowledgeGraphNode pn ON pn.CanonicalKey='part:'+LOWER(LTRIM(RTRIM(b.MaHang)))
            WHERE t.ThuMuc IN (SELECT [value] FROM OPENJSON(:departments)) AND t.Servable=1 AND t.IsCurrent=1
              AND b.TrangSo IS NOT NULL
        ) source ON target.SourceNodeID=source.SourceNodeID AND target.TargetNodeID=source.TargetNodeID
                   AND target.RelationType=source.RelationType AND target.SourceDocID=source.SourceDocID
                   AND target.SourcePage=source.SourcePage
        WHEN NOT MATCHED THEN INSERT
             (SourceNodeID,TargetNodeID,RelationType,Origin,ServingStatus,Confidence,SourceDocID,SourcePage,
              SourceVersion,Department,Site,SecurityLevel,ReviewedBy,ReviewedAt)
             VALUES(source.SourceNodeID,source.TargetNodeID,source.RelationType,'deterministic','approved',1,
                    source.SourceDocID,source.SourcePage,source.SourceVersion,source.Department,source.Site,
                    source.SecurityLevel,'deterministic-seed',SYSUTCDATETIME());
        """,
        """
        MERGE dbo.KnowledgeGraphEdge AS target
        USING (
            SELECT pn.NodeID SourceNodeID, mn.NodeID TargetNodeID, 'USES_MATERIAL' RelationType,
                   b.DocID SourceDocID, b.TrangSo SourcePage, t.VersionNo SourceVersion,
                   t.ThuMuc Department, t.Site, ISNULL(t.SecurityLevel,'confidential') SecurityLevel
            FROM dbo.BangKeVatTu b JOIN dbo.TaiLieu t ON t.DocID=b.DocID
            JOIN dbo.KnowledgeGraphNode pn ON pn.CanonicalKey='part:'+LOWER(LTRIM(RTRIM(b.MaHang)))
            JOIN dbo.KnowledgeGraphNode mn ON mn.CanonicalKey='material:'+LOWER(LTRIM(RTRIM(COALESCE(NULLIF(b.NormalizedMaterial,''),b.VatLieu))))
            WHERE t.ThuMuc IN (SELECT [value] FROM OPENJSON(:departments)) AND t.Servable=1 AND t.IsCurrent=1
              AND b.TrangSo IS NOT NULL
        ) source ON target.SourceNodeID=source.SourceNodeID AND target.TargetNodeID=source.TargetNodeID
                   AND target.RelationType=source.RelationType AND target.SourceDocID=source.SourceDocID
                   AND target.SourcePage=source.SourcePage
        WHEN NOT MATCHED THEN INSERT
             (SourceNodeID,TargetNodeID,RelationType,Origin,ServingStatus,Confidence,SourceDocID,SourcePage,
              SourceVersion,Department,Site,SecurityLevel,ReviewedBy,ReviewedAt)
             VALUES(source.SourceNodeID,source.TargetNodeID,source.RelationType,'deterministic','approved',1,
                    source.SourceDocID,source.SourcePage,source.SourceVersion,source.Department,source.Site,
                    source.SecurityLevel,'deterministic-seed',SYSUTCDATETIME());
        """,
        """
        UPDATE dbo.KnowledgeGraphEdge
        SET ServingStatus='disabled'
        WHERE Origin='deterministic' AND SourcePage <= 0;
        """,
    ]
    with db_engine.engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement), params)
        counts = conn.execute(text("""
            SELECT (SELECT COUNT(*) FROM dbo.KnowledgeGraphNode) AS nodes,
                   (SELECT COUNT(*) FROM dbo.KnowledgeGraphEdge WHERE ServingStatus='approved') AS approved_edges
        """)).mappings().one()
    return dict(counts)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--department", action="append", dest="departments")
    args = parser.parse_args(argv)
    print(seed(args.departments or DEFAULT_DEPARTMENTS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
