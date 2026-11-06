import pytest
from pydantic import ValidationError

from mech_chatbot.api.rag_server import ChatRequest
from mech_chatbot.api.routers.chat import ChatMessageRequest
from mech_chatbot.rag.execution import AccessScope, RagRequest


pytestmark = pytest.mark.unit


def test_chat_request_defaults_to_quick_search_and_rejects_unknown_mode():
    assert ChatRequest(user_question="Tìm quy trình").search_mode == "quick"

    with pytest.raises(ValidationError):
        ChatRequest(user_question="Tìm quy trình", search_mode="everything")


def test_chat_message_request_defaults_to_quick_search_and_rejects_unknown_mode():
    assert ChatMessageRequest(session_id="s1", question="Tìm quy trình").search_mode == "quick"

    with pytest.raises(ValidationError):
        ChatMessageRequest(session_id="s1", question="Tìm quy trình", search_mode="everything")


def test_continuation_cursor_requires_comprehensive_search():
    with pytest.raises(ValueError, match="continuation"):
        RagRequest(
            "Tìm quy trình",
            AccessScope(user_id=7),
            continuation_cursor="cursor-1",
        )

    request = RagRequest(
        "Tìm tất cả quy trình",
        AccessScope(user_id=7),
        search_mode="comprehensive",
        continuation_cursor="cursor-1",
    )
    assert request.search_mode == "comprehensive"
    assert request.continuation_cursor == "cursor-1"


@pytest.mark.parametrize("question", [
    "Tìm tất cả quy trình", "Liệt kê đầy đủ quy định", "Rà toàn bộ tài liệu",
    "tim tat ca quy trinh", "liet ke day du", "toan    bo quy trinh",
    "Find all procedures", "Find everything", "Tất cả tài liệu về MA-100",
])
def test_broad_request_is_comprehensive_even_with_inherited_part_ids(question):
    assert ChatRequest(user_question=question, current_part_ids=["OLD-1"]).search_mode == "comprehensive"
    assert ChatMessageRequest(session_id="s", question=question).search_mode == "comprehensive"
    assert RagRequest(question, AccessScope()).search_mode == "comprehensive"


@pytest.mark.parametrize("mode", ["", None, "QUICK", " quick ", 1, [], {}])
def test_internal_contract_rejects_unknown_modes(mode):
    with pytest.raises(ValueError, match="search_mode"):
        RagRequest("Quy trình", AccessScope(), search_mode=mode)


@pytest.mark.parametrize("cursor", ["", " ", "x" * 4097, 7])
def test_cursor_shape_is_validated_at_both_api_boundaries(cursor):
    with pytest.raises(ValidationError):
        ChatRequest(user_question="Tra cứu", search_mode="comprehensive", continuation_cursor=cursor)
    with pytest.raises(ValidationError):
        ChatMessageRequest(session_id="s", question="Tra cứu", search_mode="comprehensive", continuation_cursor=cursor)


def test_explicit_comprehensive_and_ordinary_quick_keep_their_meaning():
    assert ChatRequest(user_question="MA-100", search_mode="comprehensive").search_mode == "comprehensive"
    assert ChatRequest(user_question="Quy trình đóng gói như thế nào?").search_mode == "quick"


def test_existing_typed_action_is_not_hijacked_and_combination_is_rejected():
    body = {'user_question':'Tất cả số lượng', 'scope_total':{'scope_id':'scope-a'}}
    assert ChatRequest(**body).search_mode == 'quick'
    with pytest.raises(ValidationError, match='standalone'):
        ChatRequest(**body, search_mode='comprehensive')
    with pytest.raises(ValidationError, match='standalone'):
        ChatMessageRequest(session_id='s',question='Tất cả tài liệu',image_token='image')


def test_only_confirmed_single_candidate_resolution_suppresses_automatic_scan():
    from mech_chatbot.rag.search_mode import resolve_search_mode
    assert resolve_search_mode('quick','đầy đủ thông số MA-100',
        resolved_query_scope='single_candidate') == 'quick'
    assert resolve_search_mode('comprehensive','đầy đủ thông số MA-100',
        resolved_query_scope='single_candidate') == 'comprehensive'
