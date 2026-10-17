"""Pending extraction, audited field review and current-source Math manifests."""

from dataclasses import asdict, replace
from datetime import datetime, timezone
from decimal import Decimal, DecimalException
import json
import re

from sqlalchemy import text

from mech_chatbot.application.protected_files import DocumentAccessRecord, ProtectedFileActor, evaluate_document_access
from mech_chatbot.domain.structured_evidence import BomQuantityBinding, NumericFact, RoundingRule, ScopeManifest, SourceLocator, scope_payload, source_bindings
from ..engine import resolve_engine
from ..evidence_snapshot import evidence_read_connection


def _table(conn, name):
    return name if conn.dialect.name == "sqlite" else "dbo." + name


def _json(value):
    payload = scope_payload(value) if isinstance(value, ScopeManifest) else asdict(value) if hasattr(value, "__dataclass_fields__") else value
    return json.dumps(payload, ensure_ascii=False, default=str)


def fact_payload(fact):
    return json.loads(_json(fact))


def _fact(payload):
    value = json.loads(payload) if isinstance(payload, str) else dict(payload)
    locator = dict(value["locator"])
    if locator.get("bbox") is not None:
        locator["bbox"] = tuple(locator["bbox"])
    return NumericFact(**{**value, "value": Decimal(value["value"]) if value.get("value") is not None else None,
                          "conflicts": tuple(value.get("conflicts") or ()), "locator": SourceLocator(**locator)})


def _manifest(payload, facts):
    value = json.loads(payload)
    rule = value.get("rounding_rule")
    if value.get('quantity_binding') is not None:
        value = {**value, 'quantity_binding': BomQuantityBinding(**value['quantity_binding'])}
    if value.get('empty_source_locator') is not None:
        loc = value['empty_source_locator']
        value = {**value, 'empty_source_locator': SourceLocator(**{**loc, 'bbox': tuple(loc['bbox']) if loc.get('bbox') is not None else None})}
    return ScopeManifest(**{**value, "facts": tuple(facts), "expected_row_ids": tuple(value.get("expected_row_ids") or ()),
                            "excluded_row_reasons": tuple(tuple(item) for item in value.get("excluded_row_reasons") or ()),
                            "source_revisions": tuple(tuple(item) for item in value.get("source_revisions") or ()),
                            "rounding_rule": RoundingRule(**rule) if rule else None})


def _source(conn, doc_id, version, profile, *, review=False):
    row = conn.execute(text(f"SELECT * FROM {_table(conn, 'TaiLieu')} WHERE DocID=:doc"), {"doc": int(doc_id)}).mappings().first()
    if not row or str(row["VersionNo"]) != str(version) or (not review and not row["IsCurrent"]):
        raise PermissionError("Evidence source unavailable")
    departments = tuple(conn.execute(text(f"SELECT DeptCode FROM {_table(conn, 'PhongBanChiaSe')} WHERE DocID=:doc"), {"doc": int(doc_id)}).scalars())
    record = DocumentAccessRecord(int(doc_id), row["TenFile"], row["FilePath"], row["ThuMuc"], row["SecurityLevel"], row["Site"],
                                  row["LifecycleStatus"], row["ReviewStatus"], departments, bool(row["Servable"]),
                                  row["PublicationState"], bool(row["IsCurrent"]), row["EffectiveStatus"], row["EffectiveDate"], row["ExpiryDate"])
    if review:
        _reviewer(conn, row, profile)
        if str(row["LifecycleStatus"]).lower() not in {"draft", "pending_review", "published"} or str(row["PublicationState"]).lower() not in {"draft", "published"}:
            raise PermissionError("Evidence source unavailable")
        record = replace(record, servable=True, publication_state="published", lifecycle_status="published", review_status="approved", is_current=True,
                         effective_status="effective" if record.effective_status == "draft" else record.effective_status)
    if not evaluate_document_access(ProtectedFileActor.from_profile(profile), record, strict_site_filter=True).allowed:
        raise PermissionError("Evidence source unavailable")
    return row


def _reviewer(conn, source, profile):
    user = conn.execute(text(f"SELECT Username,IsActive FROM {_table(conn, 'Users')} WHERE UserID=:uid"), {"uid": profile.get("user_id")}).mappings().first()
    roles = set(conn.execute(text(f"SELECT r.RoleName FROM {_table(conn, 'UserRoles')} ur JOIN {_table(conn, 'Roles')} r ON r.RoleID=ur.RoleID WHERE ur.UserID=:uid"), {"uid": profile.get("user_id")}).scalars())
    if not user or not user["IsActive"] or user["Username"] != profile.get("username") or not roles.intersection({"reviewer", "knowledge_approver", "admin"}):
        raise PermissionError("Verified reviewer required")
    if source["KnowledgeApproverUserID"] != profile.get("user_id"):
        raise PermissionError("Assigned knowledge approver required")


def _scope_rows(conn, scope_id):
    scope = conn.execute(text(f"SELECT * FROM {_table(conn, 'StructuredEvidenceScope')} WHERE ScopeID=:scope"), {"scope": scope_id}).mappings().first()
    if not scope:
        raise PermissionError("Evidence source unavailable")
    facts = conn.execute(text(f"SELECT * FROM {_table(conn, 'StructuredNumericFact')} WHERE ScopeID=:scope ORDER BY FactID"), {"scope": scope_id}).mappings().all()
    return scope, facts


def _lock_scope(conn, scope_id):
    if conn.dialect.name != "sqlite":
        conn.execute(text("SELECT ScopeID FROM dbo.StructuredEvidenceScope WITH (UPDLOCK,HOLDLOCK) WHERE ScopeID=:scope"), {"scope": scope_id}).first()


def _authorize(conn, scope, rows, profile, *, review=False):
    try:
        manifest = _manifest(scope['ManifestJson'], ())
        if rows and manifest.empty_source_locator is not None:
            raise ValueError('Empty declaration cannot contain facts')
        pins = tuple((row['SourceDocID'], row['SourceVersion'], row['SourceHash']) for row in rows) if rows else tuple(
            (loc.doc_id, loc.version, digest) for loc, digest in source_bindings(manifest))
    except (ValueError, TypeError, KeyError):
        raise PermissionError('Evidence source unavailable') from None
    declared = {(str(doc), str(version)) for doc, version in manifest.source_revisions}
    actual = {(str(doc), str(version)) for doc, version, _ in pins}
    if not declared or declared != actual:
        raise PermissionError("Evidence source unavailable")
    for doc, version in declared:
        source = _source(conn, doc, version, profile, review=review)
        if not review and manifest.quantity_binding is not None and source['Site'] != manifest.quantity_binding.site:
            raise PermissionError('Evidence source unavailable')
    for doc_id, source_version, source_hash in dict.fromkeys(pins):
        exists = conn.execute(text(f"SELECT SourceHash FROM {_table(conn, 'StructuredSourceSnapshot')} WHERE DocID=:doc AND VersionNo=:version AND SnapshotID=:snapshot AND CurrentSnapshot=1"),
                              {"doc": doc_id, "version": source_version, "snapshot": scope["SnapshotID"]}).scalar()
        if exists != source_hash:
            raise PermissionError("Evidence source unavailable")
    if not review and manifest.quantity_binding is not None:
        binding = manifest.quantity_binding
        try:
            current = _quantity_target_binding(conn, {k:getattr(binding,k) for k in ('root_doc_id','product_scope','site')}, profile)
        except ValueError:
            raise PermissionError('Evidence source unavailable') from None
        if current != binding:
            raise PermissionError('Evidence source unavailable')


def _audit(conn, profile, action, entity_id, before, after, note):
    conn.execute(text(f"INSERT INTO {_table(conn, 'AuditLog')} (UserID,Username,Action,EntityType,EntityID,Details) VALUES(:uid,:name,:action,'StructuredEvidence',NULL,:details)"),
                 {"uid": profile["user_id"], "name": profile["username"], "action": action,
                  "details": _json({"evidence_id": entity_id, "before": before, "after": after, "note": note})})


def _register_snapshot(conn, doc_id, version, snapshot_id, source_hash):
    if not snapshot_id or len(snapshot_id) > 128 or not re.fullmatch(r"[a-fA-F0-9]{64}", source_hash):
        raise ValueError("Snapshot identity and SHA256 required")
    current = conn.execute(text(f"SELECT VersionNo,LifecycleStatus,IsCurrent FROM {_table(conn, 'TaiLieu')} WHERE DocID=:doc"), {"doc": int(doc_id)}).mappings().first()
    if not current or str(current["VersionNo"]) != str(version) or str(current["LifecycleStatus"]).lower() not in {"draft", "pending_review", "published"} or (current["LifecycleStatus"] == "published" and not current["IsCurrent"]):
        raise ValueError("Current or pending source revision required")
    params = {"doc": int(doc_id), "version": int(version), "snapshot": snapshot_id, "hash": source_hash}
    registered = conn.execute(text(f"SELECT SourceHash,CurrentSnapshot FROM {_table(conn, 'StructuredSourceSnapshot')} WHERE DocID=:doc AND VersionNo=:version AND SnapshotID=:snapshot"), params).mappings().first()
    if registered is not None and not registered["CurrentSnapshot"]:
        raise ValueError("A stale snapshot cannot be reactivated")
    if registered is not None and registered["SourceHash"] != source_hash:
        raise ValueError("Immutable snapshot hash mismatch")
    if registered is None:
        conn.execute(text(f"UPDATE {_table(conn, 'StructuredSourceSnapshot')} SET CurrentSnapshot=0 WHERE DocID=:doc"), params)
        conn.execute(text(f"INSERT INTO {_table(conn, 'StructuredSourceSnapshot')}(DocID,VersionNo,SnapshotID,SourceHash,CurrentSnapshot) VALUES(:doc,:version,:snapshot,:hash,1)"), params)


def register_source_snapshot(doc_id, version, snapshot_id, source_hash, *, db_engine=None):
    with resolve_engine(db_engine).begin() as conn:
        _register_snapshot(conn, doc_id, version, snapshot_id, source_hash)
    return snapshot_id


def save_extracted_scope(scope, *, db_engine=None):
    """Register immutable extraction; caller-provided approval is discarded."""
    bindings = source_bindings(scope)
    if not scope.scope_id or len(scope.scope_id) > 255 or not scope.snapshot_id or len(scope.snapshot_id) > 128 or not bindings:
        raise ValueError("Scope identity, snapshot and pinned evidence required")
    facts = tuple(replace(fact, review_status="pending") for fact in scope.facts)
    if len({fact.fact_id for fact in facts}) != len(facts) or any(len(fact.fact_id) > 450 for fact in facts):
        raise ValueError("Unique bounded fact identifiers required")
    actual = {(str(loc.doc_id), str(loc.version)) for loc, _ in bindings}
    if actual != {(str(doc),str(version)) for doc,version in scope.source_revisions} or any(not re.fullmatch(r"[a-fA-F0-9]{64}", digest) for _,digest in bindings):
        raise ValueError("Complete revision/hash binding required")
    pending = replace(scope, facts=(), expected_row_ids=(), expected_row_count=None, completeness_reviewed=False,
                      authorized=False, rounding_rule=None, excluded_row_reasons=())
    with resolve_engine(db_engine).begin() as conn:
        existing = conn.execute(text(f"SELECT ManifestJson FROM {_table(conn, 'StructuredEvidenceScope')} WHERE ScopeID=:id"), {"id": scope.scope_id}).scalar()
        if existing is not None:
            raise ValueError("Scope already exists; use a new extraction snapshot")
        for doc, version in actual:
            hashes = {digest for loc, digest in bindings if str(loc.doc_id) == doc}
            if len(hashes) != 1:
                raise ValueError("One immutable hash per source snapshot required")
            _register_snapshot(conn, doc, version, scope.snapshot_id, next(iter(hashes)))
        conn.execute(text(f"INSERT INTO {_table(conn, 'StructuredEvidenceScope')}(ScopeID,SnapshotID,ManifestJson,CompletenessReviewed,UpdatedAt) VALUES(:id,:snapshot,:manifest,0,:now)"),
                     {"id": scope.scope_id, "snapshot": scope.snapshot_id, "manifest": _json(pending), "now": datetime.now(timezone.utc)})
        for fact in facts:
            conn.execute(text(f"INSERT INTO {_table(conn, 'StructuredNumericFact')}(FactID,ScopeID,SourceDocID,SourceVersion,SourceHash,PayloadJson,ReviewStatus,UpdatedAt) VALUES(:id,:scope,:doc,:version,:hash,:payload,'pending',:now)"),
                         {"id": fact.fact_id, "scope": scope.scope_id, "doc": int(fact.locator.doc_id), "version": int(fact.locator.version), "hash": fact.source_hash, "payload": _json(fact), "now": datetime.now(timezone.utc)})
    return scope.scope_id


def _document_scopes(conn, doc_id):
    locator = "CAST(json_extract(s.ManifestJson, '$.empty_source_locator.doc_id') AS TEXT)" if conn.dialect.name == 'sqlite' else "JSON_VALUE(s.ManifestJson, '$.empty_source_locator.doc_id')"
    return conn.execute(text(
        f"SELECT DISTINCT s.* FROM {_table(conn, 'StructuredEvidenceScope')} s LEFT JOIN {_table(conn, 'StructuredNumericFact')} f ON f.ScopeID=s.ScopeID "
        f"WHERE f.SourceDocID=:doc OR {locator}=:empty_doc ORDER BY s.ScopeID"),
        {'doc': int(doc_id), 'empty_doc': str(doc_id)}).mappings().all()


def list_document_scopes(doc_id, profile, *, db_engine=None):
    with resolve_engine(db_engine).connect() as conn:
        scopes = _document_scopes(conn, doc_id)
        result = []
        for scope in scopes:
            _, rows = _scope_rows(conn, scope["ScopeID"])
            try:
                _authorize(conn, scope, rows, profile, review=True)
            except PermissionError:
                continue
            manifest = _manifest(scope["ManifestJson"], ())
            rule = manifest.rounding_rule
            binding = manifest.quantity_binding
            if binding is not None:
                try:
                    if _quantity_target_binding(conn, {k:getattr(binding,k) for k in ('root_doc_id','product_scope','site')}, profile) != binding:
                        binding = None
                except (PermissionError, ValueError):
                    binding = None
            result.append({"scope_id": manifest.scope_id, "snapshot_id": manifest.snapshot_id, "completeness_reviewed": bool(scope["CompletenessReviewed"]),
                           "definition": manifest.definition, "period": manifest.period, "unit": manifest.unit, "currency": manifest.currency,
                           "expected_row_ids": list(manifest.expected_row_ids), "rounding_scale": rule.scale if rule else None, "rounding_mode": rule.mode if rule else None,
                           "excluded_row_reasons": dict(manifest.excluded_row_reasons),
                           "quantity_binding": asdict(binding) if binding is not None else None,
                           **({k:scope_payload(manifest)[k] for k in ('empty_source_locator','empty_source_hash','empty_source_extraction_method')}
                              if manifest.empty_source_locator is not None else {})})
    return result


def list_scope_facts(scope_id, profile, *, offset=0, limit=50, db_engine=None):
    offset, limit = max(0, int(offset)), max(1, min(100, int(limit)))
    with resolve_engine(db_engine).connect() as conn:
        scope, rows = _scope_rows(conn, scope_id)
        _authorize(conn, scope, rows, profile, review=True)
        selected = rows[offset:offset + limit]
        items = [fact_payload(_fact(row["ReviewedPayloadJson"] or row["PayloadJson"])) for row in selected]
    return {"items": items, "offset": offset, "limit": limit, "has_more": len(rows) > offset + limit}


def review_numeric_fact(fact_id, profile, *, action, value_text=None, unit=None, currency=None, note, db_engine=None):
    if action not in {"approve", "reject"} or not str(note or "").strip():
        raise ValueError("Review action and note required")
    with resolve_engine(db_engine).begin() as conn:
        row = conn.execute(text(f"SELECT * FROM {_table(conn, 'StructuredNumericFact')} WHERE FactID=:id"), {"id": fact_id}).mappings().first()
        if not row:
            raise PermissionError("Evidence source unavailable")
        _lock_scope(conn, row["ScopeID"])
        row = conn.execute(text(f"SELECT * FROM {_table(conn, 'StructuredNumericFact')} WHERE FactID=:id"), {"id": fact_id}).mappings().one()
        scope, rows = _scope_rows(conn, row["ScopeID"])
        _authorize(conn, scope, rows, profile, review=True)
        original = _fact(row["ReviewedPayloadJson"] or row["PayloadJson"])
        if action == 'approve' and set(original.conflicts) & {
                'missing_transaction_identifier', 'ambiguous_transaction_identifier',
                'unverified_transaction_identifier'}:
            raise ValueError('Resolve transaction identity in a new source snapshot before approving amounts')
        if value_text is not None and (not isinstance(value_text, str) or len(value_text) > 100):
            raise ValueError("Canonical decimal text required")
        try:
            value = Decimal(value_text) if value_text is not None else original.value
        except DecimalException:
            raise ValueError("Canonical decimal text required") from None
        if value is not None and (not value.is_finite() or len(value.as_tuple().digits) > 4096
                                  or abs(value.as_tuple().exponent) > 4096):
            raise ValueError("Finite bounded decimal required")
        if action == "approve" and (value is None or not value.is_finite() or (original.conflicts and value_text is None)):
            raise ValueError("Resolve conflicts with explicit validated value before approval")
        reviewed = replace(original, value=value, unit=original.unit if unit is None else unit,
                           currency=original.currency if currency is None else currency,
                           review_status="approved" if action == "approve" else "rejected", conflicts=() if action == "approve" else original.conflicts)
        conn.execute(text(f"UPDATE {_table(conn, 'StructuredNumericFact')} SET ReviewedPayloadJson=:payload,ReviewStatus=:status,ReviewedBy=:uid,ReviewNote=:note,UpdatedAt=:now WHERE FactID=:id"),
                     {"payload": _json(reviewed), "status": reviewed.review_status, "uid": profile["user_id"], "note": note, "now": datetime.now(timezone.utc), "id": fact_id})
        conn.execute(text(f"UPDATE {_table(conn, 'StructuredEvidenceScope')} SET CompletenessReviewed=0 WHERE ScopeID=:scope"), {"scope": row["ScopeID"]})
        _audit(conn, profile, "structured_fact_review", fact_id, row["ReviewedPayloadJson"] or row["PayloadJson"], fact_payload(reviewed), note)
    return fact_payload(reviewed)


def _quantity_target_binding(conn, target, profile):
    if (not isinstance(target, dict) or set(target) != {'root_doc_id', 'product_scope', 'site'}
            or type(target['root_doc_id']) is not int or not 0 < target['root_doc_id'] <= 2147483647
            or any(not isinstance(target[k], str) or not target[k].strip() or len(target[k]) > 255
                   for k in ('product_scope', 'site'))):
        raise ValueError('An explicit bounded quantity target is required')
    version = conn.execute(text(f"SELECT VersionNo FROM {_table(conn, 'TaiLieu')} WHERE DocID=:doc"), {'doc': target['root_doc_id']}).scalar()
    if version is None:
        raise PermissionError('Evidence source unavailable')
    source = _source(conn, target['root_doc_id'], version, profile)
    hashes = conn.execute(text(f"SELECT SourceHash FROM {_table(conn, 'StructuredSourceSnapshot')} WHERE DocID=:doc AND VersionNo=:version AND CurrentSnapshot=1"),
                          {'doc': target['root_doc_id'], 'version': version}).scalars().all()
    if source['Site'] != target['site'] or len(hashes) != 1:
        raise PermissionError('Evidence source unavailable')
    return BomQuantityBinding(target['root_doc_id'], int(version), hashes[0], target['product_scope'], target['site'])


def review_scope_completeness(scope_id, profile, *, expected_row_ids, rounding_rule, definition, period="", unit=None, currency=None, note, excluded_row_reasons=None, quantity_target=None, db_engine=None):
    if not definition or not note or not isinstance(rounding_rule, RoundingRule) or type(rounding_rule.scale) is not int or not -12 <= rounding_rule.scale <= 12 or rounding_rule.mode not in {"ROUND_HALF_UP", "ROUND_HALF_EVEN", "ROUND_DOWN", "ROUND_UP", "ROUND_HALF_DOWN", "ROUND_CEILING", "ROUND_FLOOR", "ROUND_05UP"}:
        raise ValueError("Reviewed definition, note and valid rounding policy required")
    expected = tuple(str(value) for value in expected_row_ids)
    if excluded_row_reasons is not None and not isinstance(excluded_row_reasons, dict):
        raise ValueError('Excluded row reasons must be a mapping')
    excluded = tuple((excluded_row_reasons or {}).items())
    with resolve_engine(db_engine).begin() as conn:
        _lock_scope(conn, scope_id)
        scope, rows = _scope_rows(conn, scope_id)
        _authorize(conn, scope, rows, profile, review=True)
        facts = tuple(_fact(row["ReviewedPayloadJson"] or row["PayloadJson"]) for row in rows)
        manifest = _manifest(scope['ManifestJson'], ())
        actual = tuple(fact.locator.row_key for fact in facts)
        manifest = replace(manifest, excluded_row_reasons=excluded)
        excluded = tuple(sorted(excluded))
        manifest = replace(manifest, excluded_row_reasons=excluded)
        excluded_ids = set(dict(excluded))
        selected_ids = set(expected)
        if (not expected and manifest.empty_source_locator is None) or len(selected_ids) != len(expected) or len(set(actual)) != len(actual) or selected_ids & excluded_ids or selected_ids | excluded_ids != set(actual):
            raise ValueError("Exact reviewed row set required")
        facts = tuple(fact for fact in facts if fact.locator.row_key in selected_ids)
        if not all(fact.review_status == "approved" and not fact.conflicts and fact.value is not None for fact in facts):
            raise ValueError("Exact reviewed row set required")
        units, currencies = {fact.unit for fact in facts}, {fact.currency for fact in facts}
        if not facts:
            units, currencies = {unit}, {currency}
        elif (unit is not None and units != {unit}) or (currency is not None and currencies != {currency}):
            raise ValueError('Explicit unit and currency must match reviewed facts')
        if (len(units) != 1 or not isinstance(next(iter(units)), str) or not next(iter(units)).strip()
                or len(currencies) != 1 or not isinstance(next(iter(currencies)), str)):
            raise ValueError("A single reviewed unit and currency required")
        reviewed = replace(manifest, expected_row_ids=expected, expected_row_count=len(expected),
                           completeness_reviewed=True, rounding_rule=replace(rounding_rule, reviewed=True), definition=definition, period=period,
                           unit=next(iter(units)), currency=next(iter(currencies)))
        binding = _quantity_target_binding(conn, quantity_target, profile) if quantity_target is not None else None
        if binding is not None and (len(facts) != 1 or reviewed.unit != 'piece' or reviewed.currency
                or facts[0].value < 0 or facts[0].value.adjusted() > 127
                or facts[0].value != facts[0].value.to_integral_value()):
            raise ValueError('One reviewed whole piece quantity is required')
        if binding is not None and any(_source(conn, fact.locator.doc_id, fact.locator.version, profile, review=True)['Site'] != binding.site for fact in facts):
            raise PermissionError('Evidence source unavailable')
        reviewed = replace(reviewed, quantity_binding=binding)
        conn.execute(text(f"UPDATE {_table(conn, 'StructuredEvidenceScope')} SET ManifestJson=:manifest,CompletenessReviewed=1,ReviewedBy=:uid,ReviewNote=:note,UpdatedAt=:now WHERE ScopeID=:scope"),
                     {"manifest": _json(reviewed), "uid": profile["user_id"], "note": note, "now": datetime.now(timezone.utc), "scope": scope_id})
        _audit(conn, profile, "structured_scope_review", scope_id, scope["ManifestJson"], json.loads(_json(reviewed)), note)
    return scope_id


def list_approved_document_scopes(doc_id, profile, *, offset=0, limit=50, db_engine=None):
    if (type(doc_id) is not int or not 1 <= doc_id <= 2147483647
            or type(offset) is not int or not 0 <= offset <= 100000
            or type(limit) is not int or not 1 <= limit <= 50):
        raise ValueError('Invalid scope selection request')
    items = []
    visible = 0
    with evidence_read_connection(db_engine) as conn:
        scope_ids = tuple(scope['ScopeID'] for scope in _document_scopes(conn, doc_id))
        # ponytail: scan approved scopes for ACL-safe paging; add indexed serving
        # manifests if this exceeds the existing request deadline/latency gate.
        for scope_id in scope_ids:
            scope = approved_scope_in_connection(conn, scope_id, profile)
            if (scope is None or scope.expected_row_count != len(scope.facts)
                    or set(scope.expected_row_ids) != {fact.locator.row_key for fact in scope.facts}
                    or not scope.rounding_rule or not scope.rounding_rule.reviewed):
                continue
            if visible >= offset:
                items.append({'scope_id': scope.scope_id, 'definition': scope.definition,
                              'period': scope.period, 'unit': scope.unit, 'currency': scope.currency,
                              **({'quantity_binding': asdict(scope.quantity_binding)} if scope.quantity_binding else {})})
            visible += 1
            if len(items) > limit:
                break
    return {'items': items[:limit], 'offset': offset, 'limit': limit, 'has_more': len(items) > limit}


def load_approved_scope(scope_id, profile, *, db_engine=None):
    with evidence_read_connection(db_engine) as conn:
        return approved_scope_in_connection(conn, scope_id, profile)


def approved_scope_in_connection(conn, scope_id, profile):
    """Recheck a scope while an existing history read owns the connection."""
    try:
        scope, rows = _scope_rows(conn, scope_id)
        _authorize(conn, scope, rows, profile)
    except PermissionError:
        return None
    if not scope["CompletenessReviewed"] or not scope["ReviewedBy"]:
        return None
    facts = tuple(_fact(row["ReviewedPayloadJson"] or row["PayloadJson"]) for row in rows)
    manifest = _manifest(scope['ManifestJson'], facts)
    if manifest.excluded_row_reasons:
        selected = set(manifest.expected_row_ids)
        excluded = set(dict(manifest.excluded_row_reasons))
        actual = tuple(fact.locator.row_key for fact in facts)
        if (not selected or len(selected) != len(manifest.expected_row_ids) or selected & excluded
                or len(set(actual)) != len(actual) or selected | excluded != set(actual)
                or manifest.expected_row_count != len(selected)):
            return None
        rows = tuple(row for row, fact in zip(rows, facts) if fact.locator.row_key in selected)
        facts = tuple(fact for fact in facts if fact.locator.row_key in selected)
        manifest = replace(manifest, facts=facts)
    if not all(row["ReviewStatus"] == "approved" and row["ReviewedBy"] for row in rows) or any(fact.conflicts or fact.value is None for fact in facts):
        return None
    if not facts and not (manifest.empty_source_locator is not None and manifest.completeness_reviewed is True
            and type(manifest.expected_row_count) is int and manifest.expected_row_count == 0 and manifest.expected_row_ids == ()
            and isinstance(manifest.unit, str) and bool(manifest.unit.strip()) and isinstance(manifest.currency, str)
            and isinstance(manifest.definition, str) and bool(manifest.definition.strip())
            and manifest.rounding_rule is not None and manifest.rounding_rule.reviewed is True
            and type(manifest.rounding_rule.scale) is int and -12 <= manifest.rounding_rule.scale <= 12
            and manifest.rounding_rule.mode in {'ROUND_HALF_UP','ROUND_HALF_EVEN','ROUND_DOWN','ROUND_UP','ROUND_HALF_DOWN','ROUND_CEILING','ROUND_FLOOR','ROUND_05UP'}):
        return None
    return replace(manifest, authorized=True)
