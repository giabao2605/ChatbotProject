"""Document, ingestion, lifecycle and protected-file HTTP adapters."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any, Mapping

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse

from mech_chatbot.application.document_review import PublicationCommand, PublicationOutcome, ReviewDocumentsCommand, ReviewItem, normalize_publish_mode
from mech_chatbot.application.document_upload import DocumentActor, UploadDocumentCommand, UploadFailure, UploadRejected
from mech_chatbot.application.protected_files import ProtectedFileActor, ProtectedFileError, ProtectedFileReference
from mech_chatbot.api.dependencies import csrf_profile, current_profile, require_any_role
from mech_chatbot.api.transport_utils import (
    assert_any_role as _assert_any_role,
    parse_json_list as _parse_json_list,
    parse_json_obj as _parse_json_obj,
    parse_json_or_csv_list as _parse_json_or_csv_list,
    rows_to_json as _rows_to_json,
    safe_int as _safe_int,
    split_csv as _split_csv,
)
from mech_chatbot.auth.authorization import role_allows
from mech_chatbot.config.logging import logger
from mech_chatbot.services.audit_service import write_audit_log
from mech_chatbot.services.document_service import (
    archive_document,
    delete_document_completely,
    reconcile_serving_state,
    reject_document,
    update_document_common_metadata,
    validate_publish_contract,
)
from mech_chatbot.services.job_service import (
    cancel_job,
    queue_eta_seconds,
    requeue_job,
    set_job_priority,
)
from mech_chatbot.services.knowledge_governance_service import (
    update_document_governance_metadata,
    validate_document_metadata_actor,
)
from mech_chatbot.services.lifecycle_service import (
    mark_document_reviewed,
    refresh_expired_status,
    set_document_lifecycle,
)
from mech_chatbot.services.ui_query_service import (
    bulk_delete_ingestion_jobs,
    delete_ingestion_job,
    get_document_lifecycle_counts,
    list_bulk_action_jobs,
    list_bulk_meta_departments,
    list_docs_for_bulk_meta,
    list_documents,
    list_expiring_documents,
    list_ingestion_jobs,
    list_pending_review_docs,
    mark_document_expired,
    mark_job_pending_review,
    mark_job_rejected,
    reject_ingestion_job,
)


router = APIRouter(prefix="/api", tags=["operations"])
files_router = APIRouter(prefix="/api/files", tags=["files"])


def _is_admin(profile: dict[str, Any]) -> bool:
    return "admin" in [str(role).lower() for role in (profile.get("roles") or [])]


def _publication_payload(result: PublicationOutcome, response: Response | None = None) -> dict[str, Any]:
    payload = result.payload if isinstance(result, PublicationOutcome) else result.to_dict()
    if response is not None and result.ok and result.state != "published":
        response.status_code = status.HTTP_202_ACCEPTED
    return payload


def _document_actor(profile: dict[str, Any]) -> DocumentActor:
    return DocumentActor(
        user_id=_safe_int(profile.get("user_id")), username=profile.get("username"),
        roles=tuple(str(role) for role in (profile.get("roles") or []) if role),
        allowed_departments=tuple(str(dept) for dept in (profile.get("allowed_departments") or []) if dept),
    )


def _publish_document_command(*, runtime: Any, job_id: int, doc_id: int | None, publish_mode: str | None, profile: dict[str, Any]) -> PublicationOutcome:
    return runtime.publication_coordinator.publish_job(
        PublicationCommand(job_id=job_id, doc_id=doc_id, publish_mode=normalize_publish_mode(publish_mode)),
        _document_actor(profile),
    )


def _assert_metadata_actor(doc_id: int, profile: dict[str, Any]) -> None:
    allowed, reason = validate_document_metadata_actor(doc_id, profile.get("user_id"), profile.get("roles") or [])
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=reason)


def _read_upload_command(
    file: UploadFile, *, owner_department: str, shared_departments: tuple[str, ...], domain: str | None,
    security_level: str | None, process_stage: str | None, site: str | None,
    upload_metadata: Mapping[str, Any] | None,
) -> UploadDocumentCommand:
    return UploadDocumentCommand(
        file_name=file.filename or "", content=file.file.read(), owner_department=owner_department,
        shared_departments=shared_departments, domain=domain, security_level=security_level,
        process_stage=process_stage, site=site, upload_metadata=upload_metadata,
    )


def _raise_upload_failure(failure: UploadFailure) -> None:
    raise HTTPException(status_code=403 if failure.code == "unauthorized" else 400, detail=failure.message)


def _upload_error_payload(failure: UploadFailure) -> dict[str, Any]:
    return {"file_name": failure.file_name, "error": failure.message}


def _file_response(path: Path, filename: str | None = None) -> FileResponse:
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, media_type=mimetypes.guess_type(str(path))[0] or "application/octet-stream", filename=filename)


def _resolve_protected_file(request: Request, reference: ProtectedFileReference, profile: dict[str, Any]):
    try:
        return request.app.state.runtime.protected_file_resolver.resolve(reference, ProtectedFileActor.from_profile(profile))
    except ProtectedFileError as exc:
        raise HTTPException(
            status_code={"unauthorized": 403, "not_found": 404, "storage_failed": 503}.get(exc.code, 500),
            detail=exc.detail,
        ) from exc


@files_router.get("/documents/{doc_id}/pages/{page_no}")
def citation_page(doc_id: int, page_no: int, request: Request, profile: dict[str, Any] = Depends(current_profile)):
    authorized = _resolve_protected_file(request, ProtectedFileReference.page(doc_id, page_no), profile)
    if authorized.placeholder:
        svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360">
<rect width="640" height="360" fill="#111827"/><rect x="24" y="24" width="592" height="312" rx="16" fill="#1f2937" stroke="#475569"/>
<text x="320" y="160" text-anchor="middle" fill="#cbd5e1" font-family="Arial,sans-serif" font-size="24">Không có ảnh xem trước</text>
<text x="320" y="202" text-anchor="middle" fill="#94a3b8" font-family="Arial,sans-serif" font-size="18">Doc {int(doc_id)} · Trang {int(page_no)}</text>
<text x="320" y="242" text-anchor="middle" fill="#64748b" font-family="Arial,sans-serif" font-size="15">Nhấn vào nguồn để mở tài liệu gốc</text></svg>"""
        return Response(content=svg, media_type="image/svg+xml", headers={"Cache-Control": "private, no-store"})
    return _file_response(authorized.path)


@files_router.get("/documents/{doc_id}/original")
def original_document(doc_id: int, request: Request, profile: dict[str, Any] = Depends(current_profile)):
    authorized = _resolve_protected_file(request, ProtectedFileReference.original(doc_id), profile)
    return _file_response(authorized.path, filename=authorized.filename)


@files_router.get("/chat-images/{image_id}")
def chat_image(image_id: str, request: Request, profile: dict[str, Any] = Depends(current_profile)):
    authorized = _resolve_protected_file(request, ProtectedFileReference.chat_image(image_id), profile)
    return _file_response(authorized.path)


@router.get("/documents")
def documents(
    dept: str | None = None, domain: str | None = None, sec: str | None = None,
    eff_mode: str | None = None, bucket: str | None = None, soon_days: int = 30,
    search: str | None = None, profile: dict[str, Any] = Depends(current_profile),
):
    global_read_admin = _is_admin(profile)
    legacy_bucket = {"con": "effective", "sap": "expiring_soon", "het": "expired"}.get(eff_mode)
    requested_bucket = str(bucket or legacy_bucket or "").strip().lower().replace("-", "_") or None
    if requested_bucket and requested_bucket not in {"effective", "expired", "expiring_soon", "needs_review"}:
        raise HTTPException(status_code=422, detail="Invalid lifecycle bucket")
    if requested_bucket and requested_bucket != "effective" and not role_allows(profile.get("roles"), "reviewer"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Lifecycle bucket requires reviewer access")
    rows = list_documents(
        allowed_departments=profile.get("allowed_departments") or [],
        max_security_level=profile.get("max_security_level") or "public",
        allowed_sites=profile.get("allowed_sites") or [], dept=dept, domain=domain, sec=sec,
        eff_mode=eff_mode, bucket=requested_bucket, soon_days=soon_days,
        search_kw=search, global_read_admin=global_read_admin,
    )
    if global_read_admin:
        write_audit_log(
            profile.get("username"), "admin_global_read_catalog", "TaiLieu", None,
            {"result_count": len(rows), "department_filter": dept, "domain_filter": domain,
             "security_filter": sec, "effective_filter": eff_mode, "has_search": bool(search)},
        )
    return {"documents": [dict(row._mapping) if hasattr(row, "_mapping") else list(row) for row in rows]}


@router.get("/documents/lifecycle-counts")
def document_lifecycle_counts(soon_days: int = 30, profile: dict[str, Any] = Depends(current_profile)):
    counts = get_document_lifecycle_counts(
        allowed_departments=profile.get("allowed_departments") or [],
        max_security_level=profile.get("max_security_level") or "public",
        allowed_sites=profile.get("allowed_sites") or [], global_read_admin=_is_admin(profile), soon_days=soon_days,
    )
    if not role_allows(profile.get("roles"), "reviewer"):
        counts = {"effective": counts.get("effective", 0)}
    return {"counts": counts, "soon_days": max(0, min(int(soon_days), 365))}


@router.post("/documents/upload")
def documents_upload(
    request: Request, file: UploadFile = File(...), thu_muc: str = Form(...),
    domain: str | None = Form(None), security_level: str | None = Form(None),
    cong_doan: str | None = Form(None), site: str | None = Form(None),
    meta_json: str | None = Form(None), extra_departments_json: str | None = Form(None),
    profile: dict[str, Any] = Depends(csrf_profile),
):
    """Nhan file tai len tu web-ui, luu vao Uploads/<thu_muc> va tao IngestionJob
    (Status='pending') de worker xu ly. Yeu cau vai tro uploader/reviewer/admin
    + CSRF. Tra ve job_id de UI dieu huong sang trang tien trinh ingest."""
    _assert_any_role(profile, "uploader", "reviewer", "admin")
    dept = (thu_muc or "").strip()
    upload_meta = _parse_json_obj(meta_json, "meta_json")
    extra_departments = _parse_json_or_csv_list(extra_departments_json, "extra_departments_json")
    actor = _document_actor(profile)
    failure = request.app.state.runtime.document_upload.preflight(
        file_name=file.filename or "", owner_department=dept, actor=actor,
    )
    if failure is not None:
        _raise_upload_failure(failure)
    command = _read_upload_command(
        file, owner_department=dept, shared_departments=tuple(extra_departments), domain=domain,
        security_level=security_level, process_stage=cong_doan, site=site, upload_metadata=upload_meta,
    )
    try:
        receipt = request.app.state.runtime.document_upload.enqueue(command, actor)
    except UploadRejected as exc:
        _raise_upload_failure(exc.failure)
    return {"ok": True, "job_id": receipt.job_id, "file_name": receipt.file_name}


@router.post("/documents/upload-batch")
def documents_upload_batch(
    request: Request, files: list[UploadFile] = File(...), thu_muc: str | None = Form(None),
    domain: str | None = Form(None), security_level: str | None = Form(None),
    cong_doan: str | None = Form(None), site: str | None = Form(None),
    meta_json: str | None = Form(None), extra_departments_json: str | None = Form(None),
    assignments_json: str | None = Form(None), profile: dict[str, Any] = Depends(csrf_profile),
):
    _assert_any_role(profile, "uploader", "reviewer", "admin")
    if not files:
        raise HTTPException(status_code=400, detail="Chưa chọn tệp")
    if len(files) > 50:
        raise HTTPException(status_code=400, detail="Một lần upload tối đa 50 tệp")
    upload_meta = _parse_json_obj(meta_json, "meta_json")
    assignments = _parse_json_list(assignments_json, "assignments_json")
    default_dept = (thu_muc or "").strip()
    default_extra = _parse_json_or_csv_list(extra_departments_json, "extra_departments_json")
    errors: list[dict[str, Any]] = []
    commands: list[UploadDocumentCommand] = []
    actor = _document_actor(profile)
    for index, upload in enumerate(files):
        assignment = assignments[index] if index < len(assignments) and isinstance(assignments[index], dict) else {}
        dept = str(assignment.get("thu_muc") or default_dept).strip()
        if not dept:
            errors.append({"file_name": upload.filename, "error": "Thiếu phòng ban"})
            continue
        failure = request.app.state.runtime.document_upload.preflight(
            file_name=upload.filename or "", owner_department=dept, actor=actor,
        )
        if failure is not None:
            errors.append(_upload_error_payload(failure))
            continue
        extra = _split_csv(assignment.get("extra_departments") or default_extra)
        commands.append(_read_upload_command(
            upload, owner_department=dept, shared_departments=tuple(extra),
            domain=assignment.get("domain") or domain,
            security_level=assignment.get("security_level") or security_level,
            process_stage=assignment.get("cong_doan") or cong_doan,
            site=assignment.get("site") or site, upload_metadata=upload_meta,
        ))
    result = request.app.state.runtime.document_upload.enqueue_batch(tuple(commands), actor)
    created = [{"job_id": item.job_id, "file_name": item.file_name, "thu_muc": item.owner_department} for item in result.jobs]
    errors.extend(_upload_error_payload(failure) for failure in result.errors)
    return {"ok": not errors, "jobs": created, "errors": errors, "created": len(created), "failed": len(errors)}


@router.get("/ingestion/jobs")
def ingestion_jobs(status_value: str | None = None, profile: dict[str, Any] = Depends(current_profile)):
    rows = list_ingestion_jobs(
        status=status_value, is_admin=False, username=profile.get("username"),
        allowed_departments=profile.get("allowed_departments") or [],
    )
    return {"jobs": _rows_to_json(rows)}


@router.get("/documents/pending-review")
def documents_pending_review(profile: dict[str, Any] = Depends(require_any_role("reviewer", "admin"))):
    return {"documents": _rows_to_json(list_pending_review_docs())}


@router.post("/documents/reconcile-serving")
def documents_reconcile_serving(body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "platform_admin")
    return reconcile_serving_state(
        limit=_safe_int(body.get("limit")) or 500,
        worker_id=f"admin:{profile.get('username') or 'System'}",
    )


@router.get("/documents/expiring")
def documents_expiring(profile: dict[str, Any] = Depends(require_any_role("reviewer", "admin"))):
    return {"documents": _rows_to_json(list_expiring_documents())}


@router.get("/documents/bulk-meta")
def documents_bulk_meta(
    dept: str | None = None, domain: str | None = None,
    profile: dict[str, Any] = Depends(require_any_role("reviewer", "admin")),
):
    if dept:
        for suffix in (" (disabled)", " (archived)"):
            if dept.endswith(suffix):
                dept = dept[: -len(suffix)]
                break
    return {
        "documents": _rows_to_json(list_docs_for_bulk_meta(dept=dept, domain=domain)),
        "departments": list_bulk_meta_departments(),
    }


@router.patch("/documents/bulk-metadata")
def documents_bulk_metadata(body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    raw_ids = body.get("doc_ids") or []
    metadata = body.get("metadata") or {}
    if not isinstance(raw_ids, list) or not raw_ids:
        raise HTTPException(status_code=400, detail="doc_ids không hợp lệ")
    if not isinstance(metadata, dict) or not metadata:
        raise HTTPException(status_code=400, detail="metadata không hợp lệ")
    ok = 0
    fail = 0
    fields = {key: value for key, value in metadata.items() if key not in {"attributes", "domain"} and value not in (None, "")}
    for raw_id in raw_ids:
        doc_id = _safe_int(raw_id)
        if not doc_id:
            fail += 1
            continue
        try:
            _assert_metadata_actor(doc_id, profile)
            result = update_document_common_metadata(
                doc_id, reviewer=profile.get("username") or "System",
                attributes=metadata.get("attributes"), domain=metadata.get("domain"), **fields,
            )
            ok += 1 if result else 0
            fail += 0 if result else 1
        except Exception:
            logger.exception("bulk metadata update failed for doc_id=%s", doc_id)
            fail += 1
    return {"ok": fail == 0, "updated": ok, "failed": fail}


def _documents_review_bulk_impl(body: dict[str, Any], profile: dict[str, Any], runtime: Any):
    _assert_any_role(profile, "reviewer", "admin")
    items = body.get("items") or []
    action = str(body.get("action") or "").strip()
    publish_mode = normalize_publish_mode(str(body.get("publish_mode") or "standalone").strip())
    reason = str(body.get("reason") or "")
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="items không hợp lệ")
    if action not in {"publish", "reject", "delete"}:
        raise HTTPException(status_code=400, detail="action không hợp lệ")
    review_items = tuple(
        ReviewItem(
            job_id=_safe_int(item.get("job_id")) if isinstance(item, dict) else None,
            doc_id=_safe_int(item.get("doc_id")) if isinstance(item, dict) else None,
        )
        for item in items
    )
    result = runtime.review_documents.execute(
        ReviewDocumentsCommand(action=action, publish_mode=publish_mode, reason=reason, items=review_items),
        _document_actor(profile),
    )
    return {
        "ok": result.ok, "updated": result.updated, "pending": result.pending,
        "failed": result.failed, "failures": list(result.failures),
    }


@router.post("/documents/review/bulk")
def documents_review_bulk(body: dict[str, Any], request: Request, profile: dict[str, Any] = Depends(csrf_profile)):
    return _documents_review_bulk_impl(body, profile, request.app.state.runtime)


@router.patch("/documents/{doc_id}/current")
def document_set_current(doc_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Dùng publish-new-version hoặc publish-standalone để đổi tài liệu hiện hành",
    )


@router.patch("/documents/{doc_id}/expired")
def document_mark_expired(doc_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    _assert_metadata_actor(doc_id, profile)
    return {"ok": bool(mark_document_expired(doc_id, reviewer=profile.get("username") or "System"))}


@router.patch("/documents/{doc_id}/metadata")
def document_update_metadata(doc_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_metadata_actor(doc_id, profile)
    fields = {key: value for key, value in body.items() if key not in {"attributes", "domain"}}
    result = update_document_common_metadata(
        doc_id, reviewer=profile.get("username") or "System",
        attributes=body.get("attributes"), domain=body.get("domain"), **fields,
    )
    return {"ok": bool(result), "result": result}


@router.get("/documents/{doc_id}/publish-contract")
def document_publish_contract(doc_id: int, profile: dict[str, Any] = Depends(require_any_role("reviewer", "admin"))):
    return validate_publish_contract(doc_id).to_dict()


@router.patch("/documents/{doc_id}/governance")
def document_update_governance(doc_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "platform_admin")
    try:
        result = update_document_governance_metadata(
            doc_id, knowledge_owner_user_id=body.get("knowledge_owner_user_id"),
            knowledge_approver_user_id=body.get("knowledge_approver_user_id"),
            taxonomy_version=body.get("taxonomy_version"), parent_applicable=body.get("parent_applicable"),
            parent_section=body.get("parent_section"), parent_page=body.get("parent_page"),
            updated_by=profile.get("username") or "System",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not result:
        raise HTTPException(status_code=404, detail="Khong tim thay tai lieu")
    return {"ok": True}


@router.patch("/documents/{doc_id}/site")
def document_backfill_site(doc_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    """Platform-only remediation for documents blocked by the strict site gate."""
    _assert_any_role(profile, "platform_admin")
    site = str(body.get("site") or "").strip()
    if not site:
        raise HTTPException(status_code=422, detail="site la bat buoc khi backfill")
    result = update_document_common_metadata(doc_id, reviewer=profile.get("username") or "System", site=site)
    if not result:
        raise HTTPException(status_code=404, detail="Khong cap nhat duoc site cho tai lieu")
    return {"ok": True, "doc_id": doc_id, "site": site}


@router.post("/documents/{doc_id}/publish-new-version")
def document_publish_new_version(
    doc_id: int, request: Request, response: Response,
    profile: dict[str, Any] = Depends(csrf_profile),
):
    result = _publish_document_command(
        runtime=request.app.state.runtime, job_id=0, doc_id=doc_id,
        publish_mode="new_version", profile=profile,
    )
    return _publication_payload(result, response)


@router.post("/documents/{doc_id}/publish-new-variant")
def document_publish_new_variant(
    doc_id: int, request: Request, response: Response,
    profile: dict[str, Any] = Depends(csrf_profile),
):
    result = _publish_document_command(
        runtime=request.app.state.runtime, job_id=0, doc_id=doc_id,
        publish_mode="new_variant", profile=profile,
    )
    return _publication_payload(result, response)


@router.post("/documents/{doc_id}/publish-standalone")
def document_publish_standalone(
    doc_id: int, request: Request, response: Response,
    profile: dict[str, Any] = Depends(csrf_profile),
):
    result = _publish_document_command(
        runtime=request.app.state.runtime, job_id=0, doc_id=doc_id,
        publish_mode="standalone", profile=profile,
    )
    return _publication_payload(result, response)


@router.post("/documents/{doc_id}/reject")
def document_reject(doc_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    return {"ok": bool(reject_document(doc_id, reviewer=profile.get("username") or "System"))}


@router.post("/documents/{doc_id}/archive")
def document_archive(doc_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    return {"ok": bool(archive_document(doc_id, reviewer=profile.get("username") or "System"))}


@router.delete("/documents/{doc_id}")
def document_delete(doc_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "admin")
    return {"ok": bool(delete_document_completely(doc_id, reviewer=profile.get("username") or "System"))}


@router.get("/ingestion/eta")
def ingestion_eta(profile: dict[str, Any] = Depends(require_any_role("uploader", "reviewer", "admin"))):
    eta = queue_eta_seconds()
    if isinstance(eta, dict):
        return eta
    return {"pending": 0, "avg_seconds": 0, "eta_seconds": eta}


@router.get("/ingestion/bulk-action-jobs")
def ingestion_bulk_jobs(profile: dict[str, Any] = Depends(require_any_role("reviewer", "admin"))):
    return {"jobs": _rows_to_json(list_bulk_action_jobs())}


@router.post("/ingestion/jobs/bulk-delete")
def ingestion_bulk_delete(body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "admin")
    return {"ok": bool(bulk_delete_ingestion_jobs(body.get("ids") or []))}


@router.patch("/ingestion/jobs/{job_id}/priority")
def ingestion_set_priority(job_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    return {"ok": bool(set_job_priority(job_id, _safe_int(body.get("priority")) or 0))}


@router.post("/ingestion/jobs/{job_id}/cancel")
def ingestion_cancel(job_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "uploader", "reviewer", "admin")
    return {"ok": bool(cancel_job(job_id, canceled_by=profile.get("username") or "System"))}


@router.post("/ingestion/jobs/{job_id}/requeue")
def ingestion_requeue(job_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    return {"ok": bool(requeue_job(job_id))}


@router.post("/ingestion/jobs/{job_id}/pending-review")
def ingestion_pending_review(job_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    return {"ok": bool(mark_job_pending_review(job_id))}


@router.post("/ingestion/jobs/{job_id}/publish")
def ingestion_publish(
    job_id: int, request: Request, response: Response,
    profile: dict[str, Any] = Depends(csrf_profile),
):
    result = _publish_document_command(
        runtime=request.app.state.runtime, job_id=job_id, doc_id=None,
        publish_mode="standalone", profile=profile,
    )
    if result.state == "not_found":
        raise HTTPException(status_code=404, detail="Không tìm thấy tài liệu của ingestion job")
    return _publication_payload(result, response)


@router.post("/ingestion/jobs/{job_id}/reject")
def ingestion_reject(job_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    reason = str(body.get("reason") or "")
    return {"ok": bool(reject_ingestion_job(job_id, reason) or mark_job_rejected(job_id))}


@router.delete("/ingestion/jobs/{job_id}")
def ingestion_delete(job_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "admin")
    return {"ok": bool(delete_ingestion_job(job_id))}


@router.get("/lifecycle")
def lifecycle_overview(
    soon_days: int = 30,
    profile: dict[str, Any] = Depends(require_any_role("reviewer", "admin")),
):
    result = {"expired": [], "expiring_soon": [], "needs_review": [], "counts": {}}
    for bucket in ("expired", "expiring_soon", "needs_review"):
        rows = list_documents(
            allowed_departments=profile.get("allowed_departments") or [],
            max_security_level=profile.get("max_security_level") or "public",
            allowed_sites=profile.get("allowed_sites") or [],
            global_read_admin=_is_admin(profile), bucket=bucket, soon_days=soon_days,
        )
        result[bucket] = _rows_to_json(rows)
        result["counts"][bucket] = len(rows)
    return result


@router.post("/lifecycle/refresh-expired")
def lifecycle_refresh(profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "admin")
    return {"ok": bool(refresh_expired_status())}


@router.patch("/lifecycle/documents/{doc_id}")
def lifecycle_set_document(doc_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    _assert_metadata_actor(doc_id, profile)
    return {"ok": bool(set_document_lifecycle(
        doc_id, effective_date=body.get("effective_date"), expiry_date=body.get("expiry_date"),
        review_date=body.get("review_date"), reviewer=profile.get("username") or "System",
    ))}


@router.post("/lifecycle/documents/{doc_id}/reviewed")
def lifecycle_mark_reviewed(doc_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    _assert_metadata_actor(doc_id, profile)
    return {"ok": bool(mark_document_reviewed(
        doc_id, reviewer=profile.get("username") or "System",
        next_review_days=_safe_int(body.get("next_review_days")) or 180,
    ))}


__all__ = [
    "citation_page", "original_document", "chat_image", "documents",
    "document_lifecycle_counts", "documents_upload", "documents_upload_batch",
    "ingestion_jobs", "documents_pending_review", "documents_reconcile_serving",
    "documents_expiring", "documents_bulk_meta", "documents_bulk_metadata",
    "_documents_review_bulk_impl", "documents_review_bulk", "document_set_current",
    "document_mark_expired", "document_update_metadata", "document_publish_contract",
    "document_update_governance", "document_backfill_site", "document_publish_new_version",
    "document_publish_new_variant", "document_publish_standalone", "document_reject",
    "document_archive", "document_delete", "ingestion_eta", "ingestion_bulk_jobs",
    "ingestion_bulk_delete", "ingestion_set_priority", "ingestion_cancel", "ingestion_requeue",
    "ingestion_pending_review", "ingestion_publish", "ingestion_reject", "ingestion_delete",
    "lifecycle_overview", "lifecycle_refresh", "lifecycle_set_document", "lifecycle_mark_reviewed",
    "files_router", "router",
]
