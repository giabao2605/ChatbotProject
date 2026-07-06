# -*- coding: utf-8 -*-
"""rag/service.py — SHIM sau P1.2.
Noi dung that da tach sang: bootstrap, prompt, rerank, intent, retrieval, evidence_gate, pipeline.
File nay chi re-export de moi import cu (`from mech_chatbot.rag.service import X`) van chay.
"""

from mech_chatbot.rag.bootstrap import *
from mech_chatbot.rag.prompt import *
from mech_chatbot.rag.rerank import *
from mech_chatbot.rag.intent import *
from mech_chatbot.rag.retrieval import *
from mech_chatbot.rag.evidence_gate import *
from mech_chatbot.rag.pipeline import *

# --- backward-compat: cac ten truoc day service.py surface qua import passthrough ---
from mech_chatbot.config.settings import QDRANT_COLLECTION
from mech_chatbot.config.logging import logger, log_trace
from mech_chatbot.llm.llm_client import cohere_invoke, get_cohere_llm, _is_cohere_rate_limit, gpt_rerank_documents, get_llm_model_name
from mech_chatbot.db.repository import search_bom_by_code
from mech_chatbot.rag.rbac import (
    compose_retrieval_filters,
    create_rbac_filter,
    _security_filter,
    _site_filter,
    _allowed_levels,
    LEVEL_ORDER,
)
from mech_chatbot.rag.entity_resolver import (
    extract_no_code_constraints,
    resolve_candidates_from_docs,
    build_candidate_table_markdown,
)
from mech_chatbot.llm.vision_client import build_vision_model, is_retryable_error
from mech_chatbot.rag.answer_checks import (  # noqa: F401
    _safe_json_loads,
    _extract_numbers,
    extract_units_and_symbols,
    has_unsupported_units_symbols,
    KNOWN_MATERIALS,
    _known_materials,
    extract_known_materials,
    has_unsupported_materials,
    extract_codes,
    has_unsupported_codes,
    requires_source_citation,
    has_required_source_citation,
)
from mech_chatbot.rag.glossary_expand import (  # noqa: F401
    _GLOSSARY_TTL,
    _GLOSSARY_CACHE,
    _glossary_domains_for_department,
    _load_glossary_cached,
    glossary_expansion_terms,
)
from mech_chatbot.rag.context_builders import (  # noqa: F401
    _context_is_mechanical,
    _context_domain,
    build_structured_attributes_context,
    build_common_metadata_context,
    format_docs,
)
