import os
import sys
import re

path = r"c:\Users\bao.nguyen\Documents\ChatBotProject\rag_logic.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# Add active_filter capturing where filters are used
content = content.replace("search_kwargs={\"k\": base_k, \"filter\": strict_filter}", "search_kwargs={\"k\": base_k, \"filter\": strict_filter}\n                            active_filter = strict_filter")
content = content.replace("search_kwargs={\"k\": base_k * 2, \"filter\": broad_filter}", "search_kwargs={\"k\": base_k * 2, \"filter\": broad_filter}\n                            active_filter = broad_filter")
content = content.replace("search_kwargs={\"k\": base_k, \"filter\": general_filter}", "search_kwargs={\"k\": base_k, \"filter\": general_filter}\n                active_filter = general_filter")
content = content.replace("search_kwargs={\"k\": base_k, \"filter\": fallback_filter}", "search_kwargs={\"k\": base_k, \"filter\": fallback_filter}\n        active_filter = fallback_filter")

# Replace rag_end log traces
log_rag_end_guarded = """log_trace("rag_end", trace_id, final_latency_ms=int((time.time() - t_start)*1000), refusal=True, refusal_reason="post_check_numbers", docs_count=len(retrieved_docs), doc_ids=doc_ids, retrieval_mode=retrieval_mode, retrieval_scores=retrieval_scores)"""
log_rag_end_guarded_new = """log_trace("rag_end", trace_id, final_latency_ms=int((time.time() - t_start)*1000), refusal=True, refusal_reason="post_check_numbers", docs_count=len(retrieved_docs), doc_ids=doc_ids, retrieved_file_goc=[d.metadata.get("file_goc") for d in retrieved_docs], version_no=[d.metadata.get("version_no") for d in retrieved_docs], variant_code=[d.metadata.get("variant_code") for d in retrieved_docs], is_current=[d.metadata.get("is_current") for d in retrieved_docs], lifecycle_status=[d.metadata.get("lifecycle_status") for d in retrieved_docs], review_status=[d.metadata.get("review_status") for d in retrieved_docs], version_policy=intent_data.get("version_policy") if "intent_data" in locals() else None, filter_used=serialize_qdrant_filter(active_filter) if "active_filter" in locals() else None, top_k=base_k if "base_k" in locals() else None, retrieval_mode=retrieval_mode, retrieval_scores=retrieval_scores, user_department=user_department, user_roles=user_roles)"""
content = content.replace(log_rag_end_guarded, log_rag_end_guarded_new)

log_rag_end_guarded_success = """log_trace("rag_end", trace_id, final_latency_ms=int((time.time() - t_start)*1000), refusal=False, docs_count=len(retrieved_docs), doc_ids=doc_ids, retrieval_mode=retrieval_mode, retrieval_scores=retrieval_scores)"""
log_rag_end_guarded_success_new = """log_trace("rag_end", trace_id, final_latency_ms=int((time.time() - t_start)*1000), refusal=False, docs_count=len(retrieved_docs), doc_ids=doc_ids, retrieved_file_goc=[d.metadata.get("file_goc") for d in retrieved_docs], version_no=[d.metadata.get("version_no") for d in retrieved_docs], variant_code=[d.metadata.get("variant_code") for d in retrieved_docs], is_current=[d.metadata.get("is_current") for d in retrieved_docs], lifecycle_status=[d.metadata.get("lifecycle_status") for d in retrieved_docs], review_status=[d.metadata.get("review_status") for d in retrieved_docs], version_policy=intent_data.get("version_policy") if "intent_data" in locals() else None, filter_used=serialize_qdrant_filter(active_filter) if "active_filter" in locals() else None, top_k=base_k if "base_k" in locals() else None, retrieval_mode=retrieval_mode, retrieval_scores=retrieval_scores, user_department=user_department, user_roles=user_roles)"""
content = content.replace(log_rag_end_guarded_success, log_rag_end_guarded_success_new)

log_rag_end_normal = """log_trace("rag_end", trace_id, final_latency_ms=int((time.time() - t_start)*1000), refusal=False, docs_count=len(retrieved_docs), doc_ids=doc_ids, retrieved_file_goc=retrieved_file_goc, version_no=version_no, variant_code=variant_code, is_current=is_current, lifecycle_status=lifecycle_status, retrieval_mode=retrieval_mode, retrieval_scores=retrieval_scores, user_department=user_department, user_roles=user_roles)"""
log_rag_end_normal_new = """log_trace("rag_end", trace_id, final_latency_ms=int((time.time() - t_start)*1000), refusal=False, docs_count=len(retrieved_docs), doc_ids=doc_ids, retrieved_file_goc=retrieved_file_goc, version_no=version_no, variant_code=variant_code, is_current=is_current, lifecycle_status=lifecycle_status, review_status=[d.metadata.get("review_status") for d in retrieved_docs], version_policy=intent_data.get("version_policy") if "intent_data" in locals() else None, filter_used=serialize_qdrant_filter(active_filter) if "active_filter" in locals() else None, top_k=base_k if "base_k" in locals() else None, retrieval_mode=retrieval_mode, retrieval_scores=retrieval_scores, user_department=user_department, user_roles=user_roles)"""
content = content.replace(log_rag_end_normal, log_rag_end_normal_new)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("Updated log_traces.")
