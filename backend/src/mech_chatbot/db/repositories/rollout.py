"""Data-backed department rollout readiness and evaluation gates."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text

from ..engine import _ensure_engine, engine
from . import audit as _r_audit


__all__ = [
    "get_department_rollout_readiness",
    "list_department_rollout_plans",
    "record_department_evaluation_gate",
    "upsert_department_rollout_plan",
]


_GATE_MIN_SOURCE_TOP5 = 0.90
_GATE_MIN_CITATION_OR_REFUSAL = 1.00
_GATE_MIN_EVIDENCE_SUPPORT = 0.85
_WAVE_CAPACITY = {1: 3, 2: 4, 3: 4, 4: 4}
_ALLOWED_STATUS_TRANSITIONS = {
    None: {"planned", "pilot"},
    "planned": {"planned", "pilot", "dark_launch", "blocked"},
    "pilot": {"pilot", "dark_launch", "active", "blocked"},
    "dark_launch": {"dark_launch", "active", "blocked"},
    "blocked": {"blocked", "planned"},
    "active": {"active"},
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _as_int(value: Any, field_name: str, minimum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} phai la so nguyen hop le") from exc
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{field_name} phai lon hon hoac bang {minimum}")
    return parsed


def _as_rate(value: Any, field_name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} phai nam trong khoang 0..1") from exc
    if not 0 <= parsed <= 1:
        raise ValueError(f"{field_name} phai nam trong khoang 0..1")
    return parsed


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _plan_from_row(row: Any) -> dict[str, Any]:
    return {
        "department_code": _clean(row["DeptCode"]),
        "wave_number": int(row["WaveNumber"]),
        "rollout_status": _clean(row["RolloutStatus"]),
        "evaluation_question_target": int(row["EvaluationQuestionTarget"]),
        "dark_launch_started_at": _iso(row["DarkLaunchStartedAt"]),
        "activated_at": _iso(row["ActivatedAt"]),
        "updated_at": _iso(row["UpdatedAt"]),
        "updated_by": _clean(row["UpdatedBy"]),
    }


def _readiness_from_row(row: Any) -> dict[str, Any]:
    """Build the public readiness contract from one aggregate SQL row."""
    target = int(row["EvaluationQuestionTarget"] or 75)
    prerequisites = {
        "rollout_plan": row["WaveNumber"] is not None,
        "knowledge_owner": row["KnowledgeOwnerUserID"] is not None,
        "knowledge_approver": row["KnowledgeApproverUserID"] is not None,
        "taxonomy": bool(_clean(row["TaxonomyVersion"])),
        "governance_active": bool(row["GovernanceActive"]),
        "domain_profile_active": bool(row["DomainProfileActive"]),
        "domain_profile_valid": bool(row["DomainProfileValid"]),
        "site_backfill": int(row["MissingSiteCount"] or 0) == 0,
        "servable_corpus": int(row["ServableDocumentCount"] or 0) > 0,
        "evaluation_set": int(row["EvaluationQuestionCount"] or 0) >= target,
        "evaluation_gate": bool(row["GatePassed"]),
    }
    return {
        "department_code": _clean(row["DeptCode"]),
        "wave_number": int(row["WaveNumber"]) if row["WaveNumber"] is not None else None,
        "rollout_status": _clean(row["RolloutStatus"]) or "unplanned",
        "evaluation_question_target": target,
        "evaluation_question_count": int(row["EvaluationQuestionCount"] or 0),
        "servable_document_count": int(row["ServableDocumentCount"] or 0),
        "missing_site_documents": int(row["MissingSiteCount"] or 0),
        "latest_gate": {
            "batch_id": _clean(row["BatchID"]) or None,
            "question_count": int(row["GateQuestionCount"] or 0),
            "source_top5_rate": float(row["SourceTop5Rate"]) if row["SourceTop5Rate"] is not None else None,
            "citation_or_refusal_rate": float(row["CitationOrRefusalRate"]) if row["CitationOrRefusalRate"] is not None else None,
            "evidence_support_rate": float(row["EvidenceSupportRate"]) if row["EvidenceSupportRate"] is not None else None,
            "rbac_site_publication_leaks": int(row["RbacSitePublicationLeaks"] or 0),
            "passed": bool(row["GatePassed"]),
            "evaluated_at": _iso(row["EvaluatedAt"]),
        },
        "prerequisites": prerequisites,
        "missing_prerequisites": [name for name, passed in prerequisites.items() if not passed],
        "ready_for_next_wave": all(prerequisites.values()),
    }


def _validate_rollout_transition(
    *,
    wave_number: int,
    rollout_status: str,
    pending_prior_wave_departments: int,
    readiness: dict[str, Any] | None,
) -> None:
    if rollout_status not in {"dark_launch", "active"}:
        return
    if wave_number > 1 and pending_prior_wave_departments > 0:
        raise ValueError("Chua hoan tat wave truoc, khong the bat dark launch/active cho wave nay")
    if not readiness or not readiness["ready_for_next_wave"]:
        missing = readiness.get("missing_prerequisites", []) if readiness else ["rollout_plan"]
        raise ValueError(
            "Phong ban chua du dieu kien de vao dark launch/active: "
            + ", ".join(missing)
        )


def _validate_status_transition(
    current_status: str | None,
    requested_status: str,
    *,
    current_wave: int | None,
    requested_wave: int,
) -> None:
    """Reject silent rollback or reassignment of an operated rollout plan."""
    current = _clean(current_status).lower() or None
    if requested_status == "pilot" and requested_wave != 1:
        raise ValueError("Chi Wave 1 duoc su dung rollout status pilot")
    if requested_status not in _ALLOWED_STATUS_TRANSITIONS.get(current, set()):
        raise ValueError(
            f"Khong the chuyen rollout status tu {current or 'unplanned'} sang {requested_status}"
        )
    if current_wave is not None and current_wave != requested_wave and current not in {"planned", "blocked"}:
        raise ValueError("Khong the doi wave sau khi phong ban da bat dau pilot/dark launch/active")


def list_department_rollout_plans() -> list[dict[str, Any]]:
    _ensure_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT DeptCode, WaveNumber, RolloutStatus, EvaluationQuestionTarget,
                       DarkLaunchStartedAt, ActivatedAt, UpdatedAt, UpdatedBy
                FROM dbo.DepartmentRolloutPlan
                ORDER BY WaveNumber, DeptCode
                """
            )
        ).mappings().all()
    return [_plan_from_row(row) for row in rows]


def get_department_rollout_readiness(department_code: str | None = None) -> list[dict[str, Any]]:
    """Return non-mutating readiness facts for pilot and subsequent waves."""
    code = _clean(department_code)
    _ensure_engine()
    where = "WHERE d.IsActive = 1"
    params: dict[str, Any] = {}
    if code:
        where += " AND d.DeptCode = :code"
        params["code"] = code
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT d.DeptCode,
                       p.WaveNumber, p.RolloutStatus, p.EvaluationQuestionTarget,
                       g.KnowledgeOwnerUserID, g.KnowledgeApproverUserID,
                       g.TaxonomyVersion, g.IsActive AS GovernanceActive,
                       dp.IsActive AS DomainProfileActive,
                       CASE WHEN dp.IsActive = 1
                                  AND ISJSON(dp.DocumentTypesJson) = 1
                                  AND ISJSON(dp.RequiredMetadataJson) = 1
                                  AND ISJSON(dp.RouterPatternsJson) = 1
                                  AND EXISTS (SELECT 1 FROM OPENJSON(CASE WHEN ISJSON(dp.DocumentTypesJson) = 1 THEN dp.DocumentTypesJson ELSE N'[]' END))
                                  AND EXISTS (SELECT 1 FROM OPENJSON(CASE WHEN ISJSON(dp.RequiredMetadataJson) = 1 THEN dp.RequiredMetadataJson ELSE N'[]' END))
                                  AND EXISTS (SELECT 1 FROM OPENJSON(CASE WHEN ISJSON(dp.RouterPatternsJson) = 1 THEN dp.RouterPatternsJson ELSE N'[]' END))
                            THEN 1 ELSE 0 END AS DomainProfileValid,
                       (SELECT COUNT(*) FROM dbo.TaiLieu t
                        WHERE t.OwnerDepartment = d.DeptCode
                          AND t.IsCurrent = 1
                          AND t.ReviewStatus = 'approved'
                          AND t.LifecycleStatus = 'published'
                          AND t.Servable = 1
                          AND t.PublicationState = 'published'
                          AND NULLIF(LTRIM(RTRIM(t.Site)), '') IS NULL
                       ) AS MissingSiteCount,
                       (SELECT COUNT(*) FROM dbo.TaiLieu t
                        WHERE t.IsCurrent = 1
                          AND t.ReviewStatus = 'approved'
                          AND t.LifecycleStatus = 'published'
                          AND t.Servable = 1
                          AND t.PublicationState = 'published'
                          AND LOWER(ISNULL(NULLIF(LTRIM(RTRIM(t.EffectiveStatus)), ''), 'effective'))
                              IN ('active', 'effective')
                          AND (t.EffectiveDate IS NULL OR t.EffectiveDate <= CAST(GETDATE() AS DATE))
                          AND (t.ExpiryDate IS NULL OR t.ExpiryDate >= CAST(GETDATE() AS DATE))
                          AND (
                              t.OwnerDepartment = d.DeptCode
                              OR EXISTS (
                                  SELECT 1 FROM dbo.PhongBanChiaSe pb
                                  WHERE pb.DocID = t.DocID
                                    AND pb.DeptCode = d.DeptCode
                              )
                          )) AS ServableDocumentCount,
                       (SELECT COUNT(*) FROM dbo.RegressionQuestion q
                        WHERE q.Department = d.DeptCode AND q.IsActive = 1) AS EvaluationQuestionCount,
                       eg.BatchID, eg.QuestionCount AS GateQuestionCount,
                       eg.SourceTop5Rate, eg.CitationOrRefusalRate, eg.EvidenceSupportRate,
                       eg.RbacSitePublicationLeaks, eg.Passed AS GatePassed,
                       eg.EvaluatedAt
                FROM dbo.Departments d
                LEFT JOIN dbo.DepartmentRolloutPlan p ON p.DeptCode = d.DeptCode
                LEFT JOIN dbo.DepartmentKnowledgeGovernance g ON g.DeptCode = d.DeptCode
                LEFT JOIN dbo.DepartmentDomainProfile dp ON dp.DeptCode = d.DeptCode
                OUTER APPLY (
                    SELECT TOP 1 *
                    FROM dbo.DepartmentEvaluationGate e
                    WHERE e.DeptCode = d.DeptCode
                    ORDER BY e.EvaluatedAt DESC, e.GateID DESC
                ) eg
                {where}
                ORDER BY ISNULL(p.WaveNumber, 99), d.DeptCode
                """
            ),
            params,
        ).mappings().all()
    return [_readiness_from_row(row) for row in rows]


def upsert_department_rollout_plan(
    department_code: str,
    *,
    wave_number: int,
    rollout_status: str = "planned",
    evaluation_question_target: int = 75,
    updated_by: str = "System",
) -> dict[str, Any]:
    code = _clean(department_code)
    wave = _as_int(wave_number, "wave_number", 1)
    if wave > 4:
        raise ValueError("wave_number phai nam trong 1..4")
    status = _clean(rollout_status).lower()
    if status not in {"planned", "pilot", "dark_launch", "active", "blocked"}:
        raise ValueError("rollout_status khong hop le")
    target = _as_int(evaluation_question_target, "evaluation_question_target", 75)
    if not code:
        raise ValueError("Department code khong hop le")

    _ensure_engine()
    readiness = {item["department_code"]: item for item in get_department_rollout_readiness(code)}.get(code)
    params = {
        "code": code,
        "wave": wave,
        "status": status,
        "target": target,
        "updated_by": _clean(updated_by)[:100] or "System",
    }
    with engine.begin() as conn:
        lock_result = conn.execute(
            text(
                """
                DECLARE @result INT;
                EXEC @result = sys.sp_getapplock
                    @Resource = N'department-rollout-plan',
                    @LockMode = N'Exclusive',
                    @LockOwner = N'Transaction',
                    @LockTimeout = 10000;
                SELECT @result;
                """
            )
        ).scalar()
        if lock_result is not None and int(lock_result) < 0:
            raise ValueError("Khong the khoa rollout plan de cap nhat an toan")

        current = conn.execute(
            text(
                """
                SELECT WaveNumber, RolloutStatus
                FROM dbo.DepartmentRolloutPlan WITH (UPDLOCK, HOLDLOCK)
                WHERE DeptCode = :code
                """
            ),
            {"code": code},
        ).mappings().first()
        _validate_status_transition(
            current["RolloutStatus"] if current else None,
            status,
            current_wave=int(current["WaveNumber"]) if current else None,
            requested_wave=wave,
        )
        assigned = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM dbo.DepartmentRolloutPlan
                WHERE WaveNumber = :wave AND DeptCode <> :code
                """
            ),
            {"wave": wave, "code": code},
        ).scalar() or 0
        if int(assigned) >= _WAVE_CAPACITY[wave]:
            raise ValueError(f"Wave {wave} da du {_WAVE_CAPACITY[wave]} phong ban theo plan 3 -> 4 -> 4 -> 4")
        pending_prior = 0
        if status in {"dark_launch", "active"} and wave > 1:
            pending_prior = conn.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM dbo.DepartmentRolloutPlan p
                    INNER JOIN dbo.Departments d ON d.DeptCode = p.DeptCode AND d.IsActive = 1
                    WHERE p.WaveNumber < :wave AND p.RolloutStatus <> 'active'
                    """
                ),
                {"wave": wave},
            ).scalar() or 0
        _validate_rollout_transition(
            wave_number=wave,
            rollout_status=status,
            pending_prior_wave_departments=int(pending_prior),
            readiness=readiness,
        )
        conn.execute(
            text(
                """
                MERGE dbo.DepartmentRolloutPlan AS target
                USING (SELECT :code AS DeptCode) AS source
                ON target.DeptCode = source.DeptCode
                WHEN MATCHED THEN UPDATE SET
                    WaveNumber = :wave,
                    RolloutStatus = :status,
                    EvaluationQuestionTarget = :target,
                    DarkLaunchStartedAt = CASE WHEN :status = 'dark_launch' THEN COALESCE(target.DarkLaunchStartedAt, GETDATE()) ELSE target.DarkLaunchStartedAt END,
                    ActivatedAt = CASE WHEN :status = 'active' THEN COALESCE(target.ActivatedAt, GETDATE()) ELSE target.ActivatedAt END,
                    UpdatedAt = GETDATE(), UpdatedBy = :updated_by
                WHEN NOT MATCHED THEN INSERT (
                    DeptCode, WaveNumber, RolloutStatus, EvaluationQuestionTarget,
                    DarkLaunchStartedAt, ActivatedAt, UpdatedBy
                ) VALUES (
                    :code, :wave, :status, :target,
                    CASE WHEN :status = 'dark_launch' THEN GETDATE() ELSE NULL END,
                    CASE WHEN :status = 'active' THEN GETDATE() ELSE NULL END,
                    :updated_by
                );
                """
            ),
            params,
        )
    _r_audit.write_audit_log(
        params["updated_by"],
        "department_rollout_plan_upsert",
        "DepartmentRolloutPlan",
        None,
        {"department_code": code, "wave_number": wave, "rollout_status": status, "evaluation_question_target": target},
    )
    return next(
        item for item in list_department_rollout_plans() if item["department_code"] == code
    )


def record_department_evaluation_gate(
    department_code: str,
    *,
    batch_id: str,
    question_count: int,
    source_top5_rate: float,
    citation_or_refusal_rate: float,
    evidence_support_rate: float,
    rbac_site_publication_leaks: int = 0,
    notes: str | None = None,
    evaluated_by: str = "System",
) -> dict[str, Any]:
    code = _clean(department_code)
    batch = _clean(batch_id)
    if not code or not batch:
        raise ValueError("department_code va batch_id la bat buoc")
    questions = _as_int(question_count, "question_count", 0)
    source_rate = _as_rate(source_top5_rate, "source_top5_rate")
    citation_rate = _as_rate(citation_or_refusal_rate, "citation_or_refusal_rate")
    evidence_rate = _as_rate(evidence_support_rate, "evidence_support_rate")
    leaks = _as_int(rbac_site_publication_leaks, "rbac_site_publication_leaks", 0)

    target_rows = get_department_rollout_readiness(code)
    target = target_rows[0]["evaluation_question_target"] if target_rows else 75
    passed = (
        questions >= target
        and source_rate >= _GATE_MIN_SOURCE_TOP5
        and citation_rate >= _GATE_MIN_CITATION_OR_REFUSAL
        and evidence_rate >= _GATE_MIN_EVIDENCE_SUPPORT
        and leaks == 0
    )
    _ensure_engine()
    params = {
        "code": code,
        "batch": batch[:100],
        "questions": questions,
        "source": source_rate,
        "citation": citation_rate,
        "evidence": evidence_rate,
        "leaks": leaks,
        "passed": 1 if passed else 0,
        "notes": _clean(notes)[:2000] or None,
        "by": _clean(evaluated_by)[:100] or "System",
    }
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO dbo.DepartmentEvaluationGate (
                    DeptCode, BatchID, QuestionCount, SourceTop5Rate,
                    CitationOrRefusalRate, EvidenceSupportRate,
                    RbacSitePublicationLeaks, Passed, Notes, EvaluatedBy
                )
                OUTPUT INSERTED.GateID, INSERTED.EvaluatedAt
                VALUES (
                    :code, :batch, :questions, :source, :citation, :evidence,
                    :leaks, :passed, :notes, :by
                )
                """
            ),
            params,
        ).fetchone()
    result = {
        "gate_id": int(row[0]) if row else None,
        "department_code": code,
        "batch_id": params["batch"],
        "passed": passed,
        "thresholds": {
            "question_count": target,
            "source_top5_rate": _GATE_MIN_SOURCE_TOP5,
            "citation_or_refusal_rate": _GATE_MIN_CITATION_OR_REFUSAL,
            "evidence_support_rate": _GATE_MIN_EVIDENCE_SUPPORT,
            "rbac_site_publication_leaks": 0,
        },
        "evaluated_at": _iso(row[1]) if row else None,
    }
    _r_audit.write_audit_log(
        params["by"],
        "department_evaluation_gate_recorded",
        "DepartmentEvaluationGate",
        result["gate_id"],
        result,
    )
    return result
