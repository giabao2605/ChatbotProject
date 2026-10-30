"""Static compatibility facade for the historical flat service namespace.

Internal callers import the explicit feature modules.  These named re-exports
remain only because external usage is unknown; removing them requires a
separate deprecation decision.  Unlike the former migration facade, this file
does not discover modules or mutate ``globals()`` at import time.
"""

from mech_chatbot.db.engine import engine

from . import (
    access_service as _access_service,
    analytics_service as _analytics_service,
    audit_service as _audit_service,
    chat_service as _chat_service,
    document_service as _document_service,
    external_ai_service as _external_ai_service,
    feedback_service as _feedback_service,
    glossary_service as _glossary_service,
    graph_service as _graph_service,
    job_service as _job_service,
    knowledge_governance_service as _knowledge_governance_service,
    lifecycle_service as _lifecycle_service,
    material_service as _material_service,
    org_service as _org_service,
    rollout_service as _rollout_service,
    settings_service as _settings_service,
    ui_query_service as _ui_query_service,
)
from .access_service import (
    count_pending_access_requests,
    create_access_request,
    get_grant_history,
    get_user_access_requests,
    list_access_requests,
    list_users_with_access,
    resolve_access_request,
    revoke_user_clearance,
    revoke_user_department,
    set_user_clearance,
)
from .analytics_service import (
    dashboard_by_department,
    get_observability,
    get_usage_analytics,
    sc_clear_all,
    sc_stats,
)
from .audit_service import write_audit_log
from .chat_service import (
    clear_chat_history,
    get_all_sessions,
    get_chat_history,
    save_answer_evidence,
    save_answer_sources,
    save_chat_history,
    update_chat_feedback,
)
from .document_service import (
    archive_document,
    delete_document_completely,
    publish_as_new_variant,
    publish_as_new_version,
    publish_as_standalone,
    publish_document,
    reconcile_serving_state,
    reject_document,
    update_document_common_metadata,
    validate_publish_contract,
)
from .external_ai_service import (
    get_external_ai_provider_profile,
    list_external_ai_provider_profiles,
    upsert_external_ai_provider_profile,
)
from .feedback_service import (
    add_regression_question,
    cleanup_dangling_records,
    ensure_regression_question,
    get_doc_quality_ranking,
    get_regression_runs,
    list_regression_questions,
    recompute_doc_quality_scores,
    set_regression_question_active,
    upsert_golden_answer,
)
from .glossary_service import (
    delete_glossary_term,
    list_domain_glossary,
    set_glossary_active,
    upsert_glossary_term,
)
from .graph_service import (
    list_community_summaries,
    list_graph_proposals,
    load_servable_community_summaries,
    propose_community_summary,
    propose_graph_edge,
    review_community_summary,
    review_graph_proposal,
    traverse_knowledge_graph,
)
from .job_service import (
    cancel_job,
    create_ingestion_job,
    queue_eta_seconds,
    requeue_job,
    set_job_priority,
)
from .knowledge_governance_service import (
    get_department_domain_profile,
    get_department_knowledge_governance,
    list_department_domain_profiles,
    list_department_knowledge_governance,
    list_missing_site_documents,
    update_document_governance_metadata,
    upsert_department_domain_profile,
    upsert_department_knowledge_governance,
    validate_document_metadata_actor,
)
from .lifecycle_service import (
    get_lifecycle_overview,
    mark_document_reviewed,
    refresh_expired_status,
    set_document_lifecycle,
)
from .material_service import (
    add_material_synonym,
    delete_material,
    delete_material_synonym,
    list_materials,
    upsert_material,
)
from .org_service import (
    archive_department,
    get_department_summary,
    get_user_sites,
    list_known_departments,
    list_known_sites,
    reassign_department_data,
    set_department_status,
    set_user_departments,
    set_user_sites,
    upsert_department,
    upsert_site,
)
from .rollout_service import (
    get_department_rollout_readiness,
    list_department_rollout_plans,
    record_department_evaluation_gate,
    upsert_department_rollout_plan,
)
from .settings_service import (
    count_docs_by_department,
    get_all_app_settings,
    set_app_setting,
)
from .ui_query_service import (
    bulk_delete_ingestion_jobs,
    classify_feedback_and_get_source,
    count_dept_pending_jobs,
    count_dept_users,
    create_user_with_roles,
    delete_feedback,
    delete_ingestion_job,
    delete_user_account,
    fetch_sources_meta_rows,
    get_dashboard_stats,
    get_document_lifecycle_counts,
    get_role_dashboard,
    get_user_clearance,
    get_user_departments,
    get_user_roles,
    is_engine_ready,
    list_audit_logs,
    list_bulk_action_jobs,
    list_bulk_meta_departments,
    list_docs_for_bulk_meta,
    list_documents,
    list_expiring_documents,
    list_feedbacks,
    list_ingestion_jobs,
    list_pending_review_docs,
    list_recent_documents,
    list_recent_failed_jobs,
    list_users_basic,
    mark_document_expired,
    mark_job_pending_review,
    mark_job_published,
    mark_job_rejected,
    ping_database,
    reject_ingestion_job,
    set_document_current,
    set_user_active_status,
    update_user_active_and_roles,
    update_user_password,
)

__all__ = [
    "engine",
    *_access_service.__all__,
    *_analytics_service.__all__,
    *_audit_service.__all__,
    *_chat_service.__all__,
    *_document_service.__all__,
    *_external_ai_service.__all__,
    *_knowledge_governance_service.__all__,
    *_feedback_service.__all__,
    *_glossary_service.__all__,
    *_job_service.__all__,
    *_lifecycle_service.__all__,
    *_material_service.__all__,
    *_org_service.__all__,
    *_rollout_service.__all__,
    *_graph_service.__all__,
    *_settings_service.__all__,
    *_ui_query_service.__all__,
]
