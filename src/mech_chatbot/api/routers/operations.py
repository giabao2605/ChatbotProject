"""Authentication and operational browser API transport.

Persistence and domain work stay behind the service facade or request runtime;
this module intentionally contains no SQL or repository access.
"""
from __future__ import annotations

from typing import Any

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from mech_chatbot.api import app_security
from mech_chatbot.api.dependencies import csrf_profile, current_profile, public_profile, require_any_role, session_payload
from mech_chatbot.api.transport_utils import assert_any_role, rows_to_json, safe_int
from mech_chatbot.auth.core import authenticate_user, load_user_profile, update_user_preferred_language
from mech_chatbot.llm.external_ai import invalidate_external_ai_provider_profiles
import mech_chatbot.services.access_service as access_service
import mech_chatbot.services.analytics_service as analytics_service
import mech_chatbot.services.audit_service as audit_service
import mech_chatbot.services.external_ai_service as external_ai_service
import mech_chatbot.services.feedback_service as feedback_service
import mech_chatbot.services.glossary_service as glossary_service
import mech_chatbot.services.graph_service as graph_service
import mech_chatbot.services.knowledge_governance_service as knowledge_governance_service
import mech_chatbot.services.material_service as material_service
import mech_chatbot.services.org_service as org_service
import mech_chatbot.services.rollout_service as rollout_service
import mech_chatbot.services.settings_service as settings_service
import mech_chatbot.services.ui_query_service as ui_query_service

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])
router = APIRouter(prefix="/api", tags=["operations"])


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1, max_length=255)


class LanguageRequest(BaseModel):
    language: str


@auth_router.post("/login")
def login(req: LoginRequest, response: Response):
    profile = authenticate_user(req.username.strip(), req.password)
    if not profile:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sai tên đăng nhập hoặc mật khẩu")
    token, payload = app_security.create_session_token(user_id=int(profile["user_id"]), username=str(profile["username"]))
    app_security.set_session_cookie(response, token)
    return {"ok": True, "user": public_profile(profile, csrf=payload.csrf)}


@auth_router.get("/me")
def me(request: Request):
    payload = session_payload(request)
    profile = load_user_profile(user_id=payload.user_id, username=payload.username)
    if not profile:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User is inactive or invalid")
    return {"ok": True, "user": public_profile(profile, csrf=payload.csrf)}


@auth_router.post("/logout")
def logout(request: Request, response: Response):
    payload = session_payload(request)
    app_security.require_csrf(request, payload)
    app_security.clear_session_cookie(response)
    return {"ok": True}


@auth_router.patch("/me/preferences")
def update_preferences(req: LanguageRequest, profile: dict[str, Any] = Depends(csrf_profile)):
    if not update_user_preferred_language(profile.get("user_id"), req.language):
        raise HTTPException(status_code=400, detail="Invalid language")
    return {"ok": True}


@auth_router.post("/refresh")
def refresh_session(request: Request, response: Response):
    """Xoay vong (rotate) session token dua tren cookie hien tai va tra ve
    profile + csrf_token moi. Yeu cau CSRF de tranh bi lam dung tu cross-site.
    Frontend goi dinh ky/khi gan het han de giu phien lien tuc."""
    payload = session_payload(request)
    app_security.require_csrf(request, payload)
    profile = load_user_profile(user_id=payload.user_id, username=payload.username)
    if not profile:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User is inactive or invalid")
    token, new_payload = app_security.create_session_token(user_id=int(profile["user_id"]), username=str(profile["username"]))
    app_security.set_session_cookie(response, token)
    return {"ok": True, "user": public_profile(profile, csrf=new_payload.csrf)}


@router.get("/dashboard")
def dashboard(profile: dict[str, Any] = Depends(current_profile)):
    return ui_query_service.get_role_dashboard(profile)


@router.get("/analytics/usage")
def usage(days: int = 30, profile: dict[str, Any] = Depends(require_any_role("reviewer", "admin"))):
    return analytics_service.get_usage_analytics(days=days)


@router.get("/analytics/observability")
def observability(days: int = 30, profile: dict[str, Any] = Depends(require_any_role("platform_admin"))):
    return analytics_service.get_observability(days=days)


@router.get("/audit")
def audit(limit: int = 100, profile: dict[str, Any] = Depends(require_any_role("platform_admin"))):
    return {"logs": rows_to_json(ui_query_service.list_audit_logs(row_limit=limit))}


@router.post("/access/request")
def access_request(body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    result = access_service.create_access_request(user_id=profile.get("user_id"), username=profile.get("username"), request_type=str(body.get("request_type") or ""), requested_level=body.get("requested_level"), requested_dept=body.get("requested_dept"), question_text=body.get("question_text"), reason=body.get("reason"))
    if not result:
        raise HTTPException(status_code=400, detail="Could not create access request")
    return result


@router.get("/access/requests")
def access_requests(status_value: str = "pending", limit: int = 200, profile: dict[str, Any] = Depends(require_any_role("security_admin"))):
    return {"requests": rows_to_json(access_service.list_access_requests(status=status_value, limit=limit)), "pending_count": access_service.count_pending_access_requests()}


@router.get("/access/my-requests")
def my_access_requests(limit: int = 50, profile: dict[str, Any] = Depends(current_profile)):
    return {"requests": rows_to_json(access_service.get_user_access_requests(profile.get("user_id"), limit=limit))}


@router.post("/access/requests/{request_id}/resolve")
def access_request_resolve(request_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "security_admin")
    result = access_service.resolve_access_request(request_id=request_id, decision=str(body.get("decision") or ""), reviewer_username=profile.get("username"), reviewer_id=profile.get("user_id"), review_note=body.get("review_note"))
    return {"ok": bool(result), "result": result}


@router.get("/access/users")
def access_users(limit: int = 1000, profile: dict[str, Any] = Depends(require_any_role("security_admin"))):
    return {"users": rows_to_json(access_service.list_users_with_access(limit=limit))}


@router.get("/access/grants")
def access_grants(limit: int = 100, profile: dict[str, Any] = Depends(require_any_role("security_admin"))):
    return {"grants": rows_to_json(access_service.get_grant_history(limit=limit))}


@router.post("/access/users/{user_id}/revoke-clearance")
def access_revoke_clearance(user_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "security_admin")
    result = access_service.revoke_user_clearance(user_id=user_id, new_level=body.get("new_level") or "public", actor_username=profile.get("username"), actor_id=profile.get("user_id"), reason=body.get("reason"))
    return {"ok": bool(result), "result": result}


@router.post("/access/users/{user_id}/revoke-department")
def access_revoke_department(user_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "security_admin")
    result = access_service.revoke_user_department(user_id=user_id, dept=str(body.get("department") or ""), actor_username=profile.get("username"), actor_id=profile.get("user_id"), reason=body.get("reason"))
    return {"ok": bool(result), "result": result}


@router.get("/users")
def users(profile: dict[str, Any] = Depends(require_any_role("security_admin"))):
    return {"users": rows_to_json(ui_query_service.list_users_basic())}


@router.get("/users/{user_id}")
def user_detail(user_id: int, profile: dict[str, Any] = Depends(require_any_role("security_admin"))):
    return {"user_id": user_id, "roles": ui_query_service.get_user_roles(user_id), "departments": ui_query_service.get_user_departments(user_id), "clearance": ui_query_service.get_user_clearance(user_id), "sites": org_service.get_user_sites(user_id)}


@router.post("/users")
def user_create(body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "security_admin")
    password = str(body.get("password") or "")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    result = ui_query_service.create_user_with_roles(username=str(body.get("username") or "").strip(), password_hash=password_hash, display_name=body.get("display_name"), department=body.get("department"), selected_roles=body.get("roles") or [], depts=body.get("departments") or [])
    if result:
        user_id = safe_int(result.get("user_id") if isinstance(result, dict) else result)
        if user_id:
            org_service.set_user_sites(user_id, body.get("sites") or [])
            access_service.set_user_clearance(user_id, body.get("max_level") or "public")
    return {"ok": bool(result), "result": result}


@router.patch("/users/{user_id}/active")
def user_active(user_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "security_admin")
    return {"ok": bool(ui_query_service.set_user_active_status(user_id, bool(body.get("is_active")), actor_username=profile.get("username"), actor_id=profile.get("user_id")))}


@router.patch("/users/{user_id}/roles")
def user_roles(user_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "security_admin")
    return {"ok": bool(ui_query_service.update_user_active_and_roles(user_id, bool(body.get("is_active", True)), body.get("add_roles") or [], body.get("del_roles") or []))}


@router.patch("/users/{user_id}/departments")
def user_departments(user_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "security_admin")
    return {"ok": bool(org_service.set_user_departments(user_id, body.get("departments") or []))}


@router.patch("/users/{user_id}/sites")
def user_sites(user_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "security_admin")
    return {"ok": bool(org_service.set_user_sites(user_id, body.get("sites") or []))}


@router.patch("/users/{user_id}/clearance")
def user_clearance(user_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "security_admin")
    return {"ok": bool(access_service.set_user_clearance(user_id, body.get("max_level") or "public"))}


@router.patch("/users/{user_id}/password")
def user_password(user_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "security_admin")
    password = str(body.get("password") or "")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    return {"ok": bool(ui_query_service.update_user_password(user_id, password_hash))}


@router.delete("/users/{user_id}")
def user_delete(user_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "security_admin")
    return {"ok": bool(ui_query_service.delete_user_account(user_id, actor_username=profile.get("username"), actor_id=profile.get("user_id")))}


@router.get("/catalog/departments")
def catalog_departments(active_only: bool = True, profile: dict[str, Any] = Depends(current_profile)):
    return {"departments": org_service.list_known_departments(active_only=active_only)}


@router.get("/catalog/missing-site-documents")
def catalog_missing_site_documents(limit: int = 500, profile: dict[str, Any] = Depends(require_any_role("platform_admin"))):
    return {"documents": knowledge_governance_service.list_missing_site_documents(limit=limit)}


@router.get("/catalog/knowledge-governance")
def catalog_knowledge_governance(profile: dict[str, Any] = Depends(require_any_role("platform_admin"))):
    return {"governance": knowledge_governance_service.list_department_knowledge_governance()}


@router.get("/catalog/domain-profiles")
def catalog_domain_profiles(profile: dict[str, Any] = Depends(require_any_role("platform_admin"))):
    return {"profiles": knowledge_governance_service.list_department_domain_profiles()}


@router.get("/catalog/rollout/readiness")
def catalog_rollout_readiness(department_code: str | None = None, profile: dict[str, Any] = Depends(require_any_role("platform_admin"))):
    return {"departments": rollout_service.get_department_rollout_readiness(department_code)}


@router.get("/catalog/rollout/plans")
def catalog_rollout_plans(profile: dict[str, Any] = Depends(require_any_role("platform_admin"))):
    return {"plans": rollout_service.list_department_rollout_plans()}


@router.put("/catalog/departments/{code}/rollout-plan")
def catalog_rollout_plan_set(code: str, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "platform_admin")
    try:
        plan = rollout_service.upsert_department_rollout_plan(code, wave_number=body.get("wave_number"), rollout_status=body.get("rollout_status") or "planned", evaluation_question_target=body.get("evaluation_question_target") or 75, updated_by=profile.get("username") or "System")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "plan": plan}


@router.post("/catalog/departments/{code}/evaluation-gate")
def catalog_evaluation_gate_record(code: str, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "platform_admin")
    try:
        gate = rollout_service.record_department_evaluation_gate(code, batch_id=body.get("batch_id"), question_count=body.get("question_count"), source_top5_rate=body.get("source_top5_rate"), citation_or_refusal_rate=body.get("citation_or_refusal_rate"), evidence_support_rate=body.get("evidence_support_rate"), rbac_site_publication_leaks=body.get("rbac_site_publication_leaks", 0), notes=body.get("notes"), evaluated_by=profile.get("username") or "System")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "gate": gate}


@router.get("/catalog/departments/{code}/knowledge-governance")
def catalog_department_governance(code: str, profile: dict[str, Any] = Depends(require_any_role("platform_admin"))):
    result = knowledge_governance_service.get_department_knowledge_governance(code)
    if result is None:
        raise HTTPException(status_code=404, detail="Khong tim thay knowledge governance cua phong ban")
    return result


@router.put("/catalog/departments/{code}/knowledge-governance")
def catalog_department_governance_set(code: str, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "platform_admin")
    try:
        saved = knowledge_governance_service.upsert_department_knowledge_governance(code, knowledge_owner_user_id=body.get("knowledge_owner_user_id"), knowledge_approver_user_id=body.get("knowledge_approver_user_id"), taxonomy_version=body.get("taxonomy_version"), external_processing_policy=body.get("external_processing_policy") or "all_external", is_active=bool(body.get("is_active", True)), updated_by=profile.get("username") or "System")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "governance": saved}


@router.get("/catalog/departments/{code}/domain-profile")
def catalog_department_domain_profile(code: str, profile: dict[str, Any] = Depends(require_any_role("platform_admin"))):
    result = knowledge_governance_service.get_department_domain_profile(code)
    if result is None:
        raise HTTPException(status_code=404, detail="Khong tim thay domain profile cua phong ban")
    return result


@router.put("/catalog/departments/{code}/domain-profile")
def catalog_department_domain_profile_set(code: str, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "platform_admin")
    try:
        saved = knowledge_governance_service.upsert_department_domain_profile(code, document_types=body.get("document_types") or [], required_metadata=body.get("required_metadata") or [], router_patterns=body.get("router_patterns") or [], parent_context_enabled=bool(body.get("parent_context_enabled", True)), is_active=bool(body.get("is_active", True)), updated_by=profile.get("username") or "System")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "profile": saved}


@router.get("/catalog/departments/{code}")
def catalog_department(code: str, profile: dict[str, Any] = Depends(require_any_role("platform_admin"))):
    return org_service.get_department_summary(code)


@router.post("/catalog/departments")
def catalog_department_upsert(body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "platform_admin")
    return {"ok": bool(org_service.upsert_department(code=str(body.get("code") or ""), name=body.get("name"), domain=body.get("domain"), site=body.get("site"), is_active=bool(body.get("is_active", True)), status=body.get("status")))}


@router.patch("/catalog/departments/{code}/status")
def catalog_department_status(code: str, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "platform_admin")
    return {"ok": bool(org_service.set_department_status(code, status=str(body.get("status") or ""), actor=profile.get("username") or "System", force=bool(body.get("force", False))))}


@router.post("/catalog/departments/{code}/archive")
def catalog_department_archive(code: str, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "platform_admin")
    return {"ok": bool(org_service.archive_department(code, actor=profile.get("username") or "System", force=bool(body.get("force", False))))}


@router.post("/catalog/departments/reassign")
def catalog_department_reassign(body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "platform_admin")
    return {"ok": bool(org_service.reassign_department_data(source_code=str(body.get("source_code") or ""), target_code=str(body.get("target_code") or ""), actor=profile.get("username") or "System", move_users=bool(body.get("move_users", True))))}


@router.get("/catalog/sites")
def catalog_sites(active_only: bool = True, profile: dict[str, Any] = Depends(current_profile)):
    return {"sites": org_service.list_known_sites(active_only=active_only)}


@router.post("/catalog/sites")
def catalog_site_upsert(body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "platform_admin")
    return {"ok": bool(org_service.upsert_site(code=str(body.get("code") or ""), name=body.get("name"), is_active=bool(body.get("is_active", True))))}


@router.get("/glossary")
def glossary(domain: str | None = None, active_only: bool = False, profile: dict[str, Any] = Depends(current_profile)):
    return {"terms": rows_to_json(glossary_service.list_domain_glossary(domain=domain, active_only=active_only))}


@router.post("/glossary")
def glossary_upsert(body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "admin", "reviewer")
    result = glossary_service.upsert_glossary_term(term=str(body.get("term") or ""), domain=body.get("domain"), synonyms=body.get("synonyms"), expansion=body.get("expansion"), is_active=bool(body.get("is_active", True)), glossary_id=body.get("glossary_id"))
    if isinstance(result, dict):
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("message") or "Khong luu duoc thuat ngu")
        return result
    return {"ok": bool(result)}


@router.patch("/glossary/{glossary_id}/active")
def glossary_active(glossary_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "admin", "reviewer")
    return {"ok": bool(glossary_service.set_glossary_active(glossary_id, bool(body.get("is_active"))))}


@router.delete("/glossary/{glossary_id}")
def glossary_delete(glossary_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "admin")
    return {"ok": bool(glossary_service.delete_glossary_term(glossary_id))}


@router.get("/materials")
def materials(profile: dict[str, Any] = Depends(current_profile)):
    return {"materials": rows_to_json(material_service.list_materials())}


@router.post("/materials")
def material_upsert(body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "admin", "reviewer")
    return {"ok": bool(material_service.upsert_material(code=str(body.get("code") or ""), display=body.get("display"), category=body.get("category"), is_active=bool(body.get("is_active", True)), material_id=body.get("material_id")))}


@router.post("/materials/{material_id}/synonyms")
def material_synonym_add(material_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "admin", "reviewer")
    return {"ok": bool(material_service.add_material_synonym(material_id, str(body.get("synonym") or "")))}


@router.delete("/materials/{material_id}")
def material_delete(material_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "admin")
    return {"ok": bool(material_service.delete_material(material_id))}


@router.delete("/materials/synonyms/{synonym_id}")
def material_synonym_delete(synonym_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "admin", "reviewer")
    return {"ok": bool(material_service.delete_material_synonym(synonym_id))}


@router.get("/feedback")
def feedbacks(only_pending: bool = False, profile: dict[str, Any] = Depends(require_any_role("reviewer", "admin"))):
    return {"feedbacks": rows_to_json(ui_query_service.list_feedbacks(only_pending=only_pending))}


@router.post("/feedback/{feedback_id}/classify")
def feedback_classify(feedback_id: int, body: dict[str, Any], request: Request, profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "reviewer", "admin")
    correct_answer = body.get("correct_answer")
    result = ui_query_service.classify_feedback_and_get_source(feedback_id, failure_type=body.get("failure_type"), correct_answer=correct_answer, reviewer_note=body.get("reviewer_note"))
    golden_hash = None
    regression_qid = None
    if correct_answer and str(correct_answer).strip():
        row = request.app.state.runtime.app_support_queries.feedback_review_context(feedback_id)
        if row:
            question, source_doc_id, department, site = row
            golden_hash = feedback_service.upsert_golden_answer(question=question, answer=correct_answer, source_doc_id=source_doc_id, department=department, site=site, created_by=profile.get("username") or "reviewer", feedback_id=feedback_id)
            regression_qid = feedback_service.ensure_regression_question(question=question, expected_doc_id=source_doc_id, department=department, site=site, created_by=profile.get("username") or "reviewer")
    return {"result": result, "golden_hash": golden_hash, "regression_qid": regression_qid}


@router.delete("/feedback/{feedback_id}")
def feedback_delete(feedback_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "admin", "reviewer")
    return {"ok": bool(ui_query_service.delete_feedback(feedback_id))}


@router.get("/regression/questions")
def regression_questions(active_only: bool = True, profile: dict[str, Any] = Depends(require_any_role("reviewer", "admin"))):
    return {"questions": rows_to_json(feedback_service.list_regression_questions(active_only=active_only))}


@router.post("/regression/questions")
def regression_question_add(body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "reviewer", "admin")
    result = feedback_service.add_regression_question(question=str(body.get("question") or ""), expected_doc_id=body.get("expected_doc_id"), expected_keywords=body.get("expected_keywords"), department=body.get("department"), site=body.get("site"), created_by=profile.get("username") or "System")
    return {"ok": result is not None, "result": result}


@router.patch("/regression/questions/{reg_qid}/active")
def regression_question_active(reg_qid: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "reviewer", "admin")
    return {"ok": bool(feedback_service.set_regression_question_active(reg_qid, bool(body.get("is_active"))))}


@router.get("/regression/runs")
def regression_runs(batch_id: str | None = None, profile: dict[str, Any] = Depends(require_any_role("reviewer", "admin"))):
    return {"runs": rows_to_json(feedback_service.get_regression_runs(batch_id=batch_id))}


@router.post("/regression/run")
def regression_run(body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "reviewer", "admin")
    from mech_chatbot.rag.regression import run_regression_batch
    raw_limit = body.get("limit")
    limit = int(raw_limit) if raw_limit else None
    return {"ok": True, "summary": run_regression_batch(limit=limit, run_by=profile.get("username") or "System")}


@router.get("/quality/documents")
def quality_documents(limit: int = 50, worst_first: bool = True, profile: dict[str, Any] = Depends(require_any_role("reviewer", "admin"))):
    return {"documents": rows_to_json(feedback_service.get_doc_quality_ranking(limit=limit, worst_first=worst_first))}


@router.post("/quality/recompute")
def quality_recompute(profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "admin", "reviewer")
    recomputed = feedback_service.recompute_doc_quality_scores()
    return {"ok": recomputed is not None, "recomputed": recomputed}


@router.post("/quality/cleanup")
def quality_cleanup(profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "platform_admin")
    return feedback_service.cleanup_dangling_records()


@router.get("/analytics/departments")
def analytics_departments(profile: dict[str, Any] = Depends(require_any_role("reviewer", "admin"))):
    return analytics_service.dashboard_by_department()


@router.get("/analytics/cache")
def analytics_cache(profile: dict[str, Any] = Depends(require_any_role("platform_admin"))):
    return analytics_service.sc_stats()


@router.get("/settings")
def settings(profile: dict[str, Any] = Depends(require_any_role("platform_admin"))):
    return {"settings": settings_service.get_all_app_settings()}


@router.get("/settings/external-ai-policy")
def external_ai_policy(profile: dict[str, Any] = Depends(require_any_role("platform_admin"))):
    """Metadata-only provider policy status for the admin settings screen."""
    return {"profiles": external_ai_service.list_external_ai_provider_profiles()}


@router.put("/settings/external-ai-policy/{provider}")
def external_ai_policy_set(provider: str, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "platform_admin")
    try:
        saved = external_ai_service.upsert_external_ai_provider_profile(provider, endpoint=body.get("endpoint"), default_model=body.get("default_model"), secret_reference=body.get("secret_reference"), allowed_surfaces=body.get("allowed_surfaces") or [], retention_mode=body.get("retention_mode"), policy_version=body.get("policy_version"), approved_by=body.get("approved_by"), risk_acceptance_ref=body.get("risk_acceptance_ref"), review_expires_at=body.get("review_expires_at"), is_active=bool(body.get("is_active", True)), updated_by=profile.get("username") or "System")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    invalidate_external_ai_provider_profiles()
    return {"ok": True, "profile": saved}


@router.put("/settings/{key}")
def setting_set(key: str, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    assert_any_role(profile, "platform_admin")
    return {"ok": bool(settings_service.set_app_setting(key, body.get("value"), updated_by=profile.get("username") or "System"))}


@router.get("/admin/graph/proposals")
def graph_proposals(status_value: str = "pending", limit: int = 100, profile: dict[str, Any] = Depends(require_any_role("knowledge_approver", "reviewer", "admin"))):
    assert_any_role(profile, "knowledge_approver", "reviewer", "admin")
    return {"proposals": rows_to_json(graph_service.list_graph_proposals(status=status_value, limit=limit))}


def _review_graph_proposal_endpoint(proposal_id: int, action: str, body: dict[str, Any], profile: dict[str, Any]):
    assert_any_role(profile, "knowledge_approver", "reviewer", "admin")
    result = graph_service.review_graph_proposal(proposal_id, action, reviewer=profile.get("username") or "System", note=body.get("note"))
    if not result.get("ok") and result.get("reason") == "not_found":
        raise HTTPException(status_code=404, detail="Graph proposal not found")
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("reason") or "Graph proposal conflict")
    audit_service.write_audit_log(action=f"graph_proposal_{action}", username=profile.get("username") or "System", entity_type="graph_proposal", entity_id=int(proposal_id), details={"status": action}, user_id=profile.get("user_id"))
    return result


@router.post("/admin/graph/proposals/{proposal_id}/approve")
def graph_proposal_approve(proposal_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    return _review_graph_proposal_endpoint(proposal_id, "approve", body, profile)


@router.post("/admin/graph/proposals/{proposal_id}/reject")
def graph_proposal_reject(proposal_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    return _review_graph_proposal_endpoint(proposal_id, "reject", body, profile)


@router.get("/admin/graph/community-summaries")
def community_summaries(status_value: str = "pending", limit: int = 100, profile: dict[str, Any] = Depends(require_any_role("knowledge_approver", "reviewer", "admin"))):
    assert_any_role(profile, "knowledge_approver", "reviewer", "admin")
    return {"summaries": rows_to_json(graph_service.list_community_summaries(status=status_value, limit=limit))}


def _review_community_summary_endpoint(summary_id: int, action: str, body: dict[str, Any], profile: dict[str, Any]):
    assert_any_role(profile, "knowledge_approver", "reviewer", "admin")
    result = graph_service.review_community_summary(summary_id, action, reviewer=profile.get("username") or "System", note=body.get("note"))
    if not result.get("ok") and result.get("reason") == "not_found":
        raise HTTPException(status_code=404, detail="Community summary not found")
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("reason") or "Community summary conflict")
    audit_service.write_audit_log(action=f"graph_community_summary_{action}", username=profile.get("username") or "System", entity_type="graph_community_summary", entity_id=int(summary_id), details={"status": action}, user_id=profile.get("user_id"))
    return result


@router.post("/admin/graph/community-summaries/{summary_id}/approve")
def community_summary_approve(summary_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    return _review_community_summary_endpoint(summary_id, "approve", body, profile)


@router.post("/admin/graph/community-summaries/{summary_id}/reject")
def community_summary_reject(summary_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    return _review_community_summary_endpoint(summary_id, "reject", body, profile)


__all__ = ["auth_router", "router"]
