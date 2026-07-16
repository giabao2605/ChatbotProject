"""Validate a controlled-demo manifest against an explicitly mapped live corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


def _mapped(value, aliases):
    return aliases.get(str(value), value)


def _symbols(aliases, documents):
    docs_by_name = {str(row.get("TenFile") or "").casefold(): row for row in documents}
    resolved = {}
    for alias, expected in aliases.items():
        document = docs_by_name.get(str(expected.get("document") or "").casefold())
        if not document:
            continue
        doc_id = int(document["DocID"])
        page = int(expected.get("page") or 1)
        resolved[f"$DOC:{alias}"] = doc_id
        resolved[f"$PAGE:{alias}"] = f"D{doc_id}P{page}"
    return resolved


def _resolve(value, symbols):
    if isinstance(value, str):
        return symbols.get(value, value)
    if isinstance(value, list):
        return [_resolve(item, symbols) for item in value]
    if isinstance(value, dict):
        return {key: _resolve(item, symbols) for key, item in value.items()}
    return value


def _referenced_documents(case, aliases):
    referenced = set()
    for name in (case.get("expected_document"), *(case.get("expected_sources") or [])):
        if isinstance(name, str) and name:
            referenced.add(name)

    def visit(value):
        if isinstance(value, str):
            if value.startswith(("$DOC:", "$PAGE:")):
                expected = aliases.get(value.split(":", 1)[1])
                if expected and expected.get("document"):
                    referenced.add(expected["document"])
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            document = value.get("document")
            if isinstance(document, str) and document:
                referenced.add(document)
            for item in value.values():
                visit(item)

    visit(case)
    return referenced


def _case_alias_mismatches(case, expected, *, site_aliases):
    comparisons = {
        "expected_page": expected.get("page"),
        "expected_version": expected.get("version"),
        "expected_department": expected.get("department"),
        "expected_site": _mapped(expected.get("site"), site_aliases),
        "expected_security_level": expected.get("security_level"),
    }
    mismatches = []
    for field, fixture_value in comparisons.items():
        if field not in case:
            continue
        case_value = case[field]
        if field == "expected_site":
            case_value = _mapped(case_value, site_aliases)
        if field in {"expected_page", "expected_version"} and (
            case_value is not None and fixture_value is not None
        ):
            case_value = int(case_value)
            fixture_value = int(fixture_value)
        if case_value != fixture_value:
            mismatches.append({
                "case_id": case.get("id"),
                "document": expected.get("document"),
                "reason": "manifest_fixture_mismatch",
                "field": field,
                "manifest_value": case_value,
                "fixture_value": fixture_value,
            })
    return mismatches


def check_fixture_cases(
    cases,
    aliases,
    sql_documents,
    qdrant_points,
    *,
    collection,
    site_aliases=None,
    source_aliases=None,
):
    """Check only documents referenced by live cases and resolve manifest symbols."""
    site_aliases = dict(site_aliases or {})
    source_aliases = dict(source_aliases or {})
    documents = {str(row.get("TenFile") or "").casefold(): row for row in sql_documents}
    expected_by_name = {
        str(value.get("document") or "").casefold(): value
        for value in aliases.values()
        if value.get("document")
    }
    points_by_doc = {}
    for point in qdrant_points:
        points_by_doc.setdefault(int(point.get("doc_id") or 0), []).append(point)

    failures = []
    checked = set()
    for case in cases:
        primary_expected = expected_by_name.get(
            str(case.get("expected_document") or "").casefold()
        )
        if primary_expected:
            failures.extend(_case_alias_mismatches(
                case, primary_expected, site_aliases=site_aliases,
            ))
        for filename in _referenced_documents(case, aliases):
            key = str(filename).casefold()
            if key in checked:
                continue
            checked.add(key)
            expected = expected_by_name.get(key)
            document = documents.get(key)
            if not expected:
                failures.append({"document": filename, "reason": "fixture_alias_missing"})
                continue
            if not document:
                failures.append({"document": filename, "reason": "sql_document_missing"})
                continue

            expected_site = _mapped(expected.get("site"), site_aliases)
            expected_source = _mapped(expected.get("source_system"), source_aliases)
            expected_review = _mapped(
                expected.get("review_status"), {"pending": "pending_review"}
            )
            expected_publication = _mapped(
                expected.get("publication_state"), {"unpublished": "draft"}
            )
            sql_valid = all((
                document.get("OwnerDepartment") == expected.get("department"),
                document.get("Site") == expected_site,
                document.get("SourceSystem") == expected_source,
                document.get("SecurityLevel") == expected.get("security_level"),
                int(document.get("VersionNo") or 0) == int(expected.get("version") or 0),
                str(document.get("LifecycleStatus") or "").casefold()
                == str(expected.get("lifecycle_status") or "").casefold(),
                str(document.get("ReviewStatus") or "").casefold()
                == str(expected_review or "").casefold(),
                str(document.get("PublicationState") or "").casefold()
                == str(expected_publication or "").casefold(),
                bool(document.get("IsCurrent")) == bool(expected.get("is_current")),
                bool(document.get("Servable")) == bool(expected.get("servable")),
            ))
            if not sql_valid:
                failures.append({"document": filename, "reason": "sql_provenance_invalid"})

            page = int(expected.get("page") or 1)
            point_valid = any(all((
                int(
                    point.get("page")
                    or point.get("page_number")
                    or point.get("trang_so")
                    or 0
                ) == page,
                point.get("owner_department") == expected.get("department"),
                point.get("site") == expected_site,
                point.get("source_system") == expected_source,
                point.get("security_level") == expected.get("security_level"),
                int(point.get("version_no") or 0) == int(expected.get("version") or 0),
                str(point.get("lifecycle_status") or "").casefold()
                == str(expected.get("lifecycle_status") or "").casefold(),
                str(point.get("review_status") or "").casefold()
                == str(expected_review or "").casefold(),
                str(point.get("publication_state") or "").casefold()
                == str(expected_publication or "").casefold(),
                bool(point.get("is_current")) == bool(expected.get("is_current")),
                bool(point.get("servable")) == bool(expected.get("servable")),
            )) for point in points_by_doc.get(int(document["DocID"]), []))
            if not point_valid:
                failures.append({"document": filename, "reason": "qdrant_provenance_invalid"})

    symbols = _symbols(aliases, sql_documents)
    resolution_fields = (
        "expected_citations", "expected_claims", "expected_branches",
        "expected_calculation", "expected_relation", "expected_relations",
    )
    resolutions = {
        case["id"]: {
            field: _resolve(case[field], symbols)
            for field in resolution_fields
            if field in case
        }
        for case in cases
    }
    for case in cases:
        resolution = resolutions[case["id"]]
        if "allowed_sites" in case:
            resolution["allowed_sites"] = [
                _mapped(site, site_aliases) for site in case["allowed_sites"]
            ]
        if "expected_site" in case:
            resolution["expected_site"] = _mapped(case["expected_site"], site_aliases)
    fingerprint_documents = sorted(
        ({key: row.get(key) for key in (
            "DocID", "TenFile", "FamilyID", "VersionNo", "LifecycleStatus",
            "ReviewStatus", "PublicationState", "IsCurrent", "Servable",
            "SourceSystem", "OwnerDepartment", "Site", "SecurityLevel",
        )} for row in sql_documents),
        key=lambda row: (str(row.get("TenFile")), int(row.get("DocID") or 0)),
    )
    fingerprint_points = sorted(
        ({key: point.get(key) for key in (
            "doc_id", "page", "page_number", "trang_so", "version_no",
            "lifecycle_status", "review_status", "publication_state",
            "is_current", "servable", "source_system", "owner_department",
            "site", "security_level", "chunk_index", "content_hash",
        )} for point in qdrant_points),
        key=lambda point: (
            int(point.get("doc_id") or 0),
            int(
                point.get("page")
                or point.get("page_number")
                or point.get("trang_so")
                or 0
            ),
            str(point.get("chunk_index") or ""),
            str(point.get("content_hash") or ""),
        ),
    )
    fingerprint = hashlib.sha256(json.dumps({
        "collection": collection,
        "documents": fingerprint_documents,
        "points": fingerprint_points,
        "site_aliases": site_aliases,
        "source_aliases": source_aliases,
    }, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return {
        "schema": "controlled-demo-main-preflight-v1",
        "passed": not failures,
        "collection": collection,
        "checked_cases": len(cases),
        "checked_documents": len(checked),
        "failures": failures,
        "case_resolutions": resolutions,
        "site_aliases": site_aliases,
        "source_aliases": source_aliases,
        "fixture_fingerprint": fingerprint,
    }


def _json_env(name, default):
    raw = os.getenv(name)
    return json.loads(raw) if raw else default


def run_live_preflight(cases):
    if os.getenv("CONTROLLED_DEMO_LIVE_OPT_IN") != "1":
        raise RuntimeError("set CONTROLLED_DEMO_LIVE_OPT_IN=1 for live corpus access")
    alias_path = Path(os.environ["CONTROLLED_DEMO_FIXTURE_ALIASES"])
    aliases = json.loads(alias_path.read_text(encoding="utf-8"))

    from sqlalchemy import text
    from qdrant_client import models
    from mech_chatbot.config.settings import QDRANT_COLLECTION
    from mech_chatbot.db.engine import engine
    from mech_chatbot.db.repositories.qdrant import _get_qdrant_client

    source_aliases = _json_env(
        "CONTROLLED_DEMO_SOURCE_ALIASES", {"controlled-demo-v2": "upload"}
    )
    site_aliases = _json_env(
        "CONTROLLED_DEMO_SITE_ALIASES",
        {"DEMO-HQ": "HQ", "DEMO-BRANCH-B": "BRANCH-B"},
    )
    expected_names = {
        str(value.get("document") or "").casefold()
        for value in aliases.values()
        if value.get("document")
    }
    with engine.connect() as connection:
        documents = [dict(row) for row in connection.execute(text("""
            SELECT DocID, TenFile, FamilyID, VersionNo, LifecycleStatus,
                   ReviewStatus, PublicationState, IsCurrent, Servable,
                   SourceSystem, OwnerDepartment, Site, SecurityLevel
            FROM dbo.TaiLieu WHERE LifecycleStatus <> 'deleting'
        """)).mappings().all() if str(row["TenFile"] or "").casefold() in expected_names]
    client = _get_qdrant_client()
    points = []
    for document in documents:
        found, _ = client.scroll(
            collection_name=QDRANT_COLLECTION,
            scroll_filter=models.Filter(must=[models.FieldCondition(
                key="metadata.doc_id", match=models.MatchValue(value=int(document["DocID"]))
            )]),
            limit=100,
            with_payload=True,
            with_vectors=False,
        )
        points.extend(dict((point.payload or {}).get("metadata") or {}) for point in found)
    return check_fixture_cases(
        cases, aliases, documents, points, collection=QDRANT_COLLECTION,
        site_aliases=site_aliases, source_aliases=source_aliases,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    from scripts.eval.run_eval import load_manifest_files
    report = run_live_preflight(load_manifest_files(args.manifest))
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
