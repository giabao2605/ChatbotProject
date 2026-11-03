"""Fresh, authorized SQL source catalog for bounded comprehensive scans."""

import json

from sqlalchemy import text

from mech_chatbot.auth.authorization import normalized_roles
from mech_chatbot.auth.core import load_user_profile
from mech_chatbot.rag.comprehensive import CursorMismatch, fingerprint
from ..engine import resolve_engine
from .ui_queries import _document_access_filters


from mech_chatbot.db.scan_connection import scan_connection, checkpoint as _checkpoint


def _query(conn, statement, params):
    rows = conn.execute(statement, params).mappings().all()
    _checkpoint()
    return rows


def _validate_actor(scope):
    actor = load_user_profile(scope.get('user_id'), scope.get('username'), strict=True)
    if not actor:
        raise CursorMismatch('Access scope changed; restart the scan')
    roles = normalized_roles(actor.get('roles'))
    requested = normalized_roles(scope.get('roles'))
    if not requested.issubset(roles):
        raise CursorMismatch('Access scope changed; restart the scan')
    if 'admin' not in roles:
        for key in ('allowed_departments', 'allowed_sites'):
            if not set(scope.get(key) or ()).issubset(set(actor.get(key) or ())):
                raise CursorMismatch('Access scope changed; restart the scan')
    levels = {'public':0, 'internal':1, 'confidential':2}
    level = scope.get('max_security_level', 'public')
    if level not in levels or levels[level] > levels.get(actor.get('max_security_level'), -1):
        raise CursorMismatch('Access scope changed; restart the scan')
    return actor


def _page_identity(pages, report):
    try:
        report = json.loads(report) if isinstance(report, str) else report
        expected = report.get('total_pages') if isinstance(report, dict) else None
        complete = (type(expected) is int and expected > 0 and report.get('status') == 'success'
                    and [int(page['PageNo']) for page in pages] == list(range(1, expected + 1))
                    and all(str(page.get('ExtractionStatus') or '').lower() == 'success'
                            and (page.get('HasContent') if 'HasContent' in page else
                                 any(str(page.get(key) or '').strip() for key in
                                     ('TextExtract', 'LocalOCRText', 'VisionSummary'))) for page in pages))
    except (ValueError, TypeError):
        expected, complete = None, False
    return {'PageCount':len(pages), 'ExpectedPageCount':expected,
            'PagesFingerprint':fingerprint(pages), 'ExtractionComplete':bool(complete)}


def list_comprehensive_catalog(*, db_engine=None, **scope):
    """Never infer completeness from top-k or from a contiguous stored prefix."""
    actor = _validate_actor(scope)
    params = {}
    admin = 'admin' in normalized_roles(actor.get('roles')) and 'admin' in normalized_roles(scope.get('roles'))
    filters = _document_access_filters(
        allowed_departments=scope.get('allowed_departments'),
        max_security_level=scope.get('max_security_level', 'public'),
        allowed_sites=scope.get('allowed_sites'), global_read_admin=admin,
        params=params, strict_site_filter=True)
    filters.extend(["LOWER(COALESCE(d.EffectiveStatus,'effective')) IN ('active','effective')",
                    '(d.EffectiveDate IS NULL OR d.EffectiveDate <= GETDATE())',
                    '(d.ExpiryDate IS NULL OR d.ExpiryDate > GETDATE())'])
    # AccessScope.department is a display label; grants define the readable scope.
    for key, column in (('site','Site'),):
        if scope.get(key):
            filters.append(f'd.{column}=:{key}')
            params[key] = scope[key]
    selected = resolve_engine(db_engine)
    # One serializable transaction binds publication, extraction and page contents.
    with scan_connection(selected, serializable=True) as conn:
        rows = _query(conn, text(f'''SELECT d.DocID,d.VersionNo,d.Site,d.ServingEpoch,
            d.PublicationVersion,d.TenFile,d.Title,d.ThuMuc,d.SecurityLevel,d.ExternalProcessingPolicy,
            s.SnapshotID,s.SourceHash,j.ExtractionReport
            FROM dbo.TaiLieu d
            OUTER APPLY (SELECT TOP 1 SnapshotID,SourceHash FROM dbo.StructuredSourceSnapshot s
                WHERE s.DocID=d.DocID AND s.VersionNo=d.VersionNo AND s.CurrentSnapshot=1
                ORDER BY SnapshotID) s
            OUTER APPLY (SELECT TOP 1 ExtractionReport FROM dbo.IngestionJobs j
                WHERE j.TenFile=d.TenFile AND j.ThuMuc=d.ThuMuc AND j.FilePath=d.FilePath
                  AND NULLIF(LTRIM(RTRIM(d.FilePath)),'') IS NOT NULL AND j.Status='published'
                ORDER BY j.JobID DESC) j
            WHERE {' AND '.join(filters)} ORDER BY d.DocID'''), params)
        catalog = []
        for row in rows:
            _checkpoint()
            pages = [dict(page) for page in _query(conn, text('''SELECT p.PageID,p.PageNo,
                CONVERT(VARCHAR(64),HASHBYTES('SHA2_256',(SELECT p.TextExtract,p.LocalOCRText,
                    p.VisionSummary FOR JSON PATH, WITHOUT_ARRAY_WRAPPER)),2) AS ContentHash,
                CASE WHEN COALESCE(NULLIF(LTRIM(RTRIM(p.TextExtract)),''),
                    NULLIF(LTRIM(RTRIM(p.LocalOCRText)),''),NULLIF(LTRIM(RTRIM(p.VisionSummary)),''))
                    IS NULL THEN 0 ELSE 1 END AS HasContent,
                p.ExtractionStatus,p.UpdatedAt FROM dbo.DocumentPages p
                WHERE p.DocID=:doc ORDER BY p.PageNo,p.PageID'''), {'doc':row['DocID']})]
            identity = _page_identity(pages, row['ExtractionReport'])
            _checkpoint()
            try:
                report = json.loads(row['ExtractionReport'] or '{}')
                binding = report.get('structured_source_snapshot') or {}
                report_matches = (str(binding.get('doc_id')) == str(row['DocID'])
                                  and str(binding.get('version')) == str(row['VersionNo'])
                                  and binding.get('snapshot_id') == row['SnapshotID']
                                  and binding.get('source_hash') == row['SourceHash']
                                  and binding.get('status') == 'registered')
            except (TypeError, ValueError, AttributeError):
                report_matches = False
            if not row['SnapshotID'] or not row['SourceHash'] or not report_matches:
                identity = {**identity, 'ExtractionComplete':False}
            catalog.append({**dict(row), **identity})
    return catalog
