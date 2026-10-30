"""Numeric prose cannot borrow another result's value or another branch's source."""
import pytest
from langchain_core.documents import Document
from mech_chatbot.rag.query_dependencies import has_dependent_numeric_claim


@pytest.mark.parametrize('answer, rejected', [
    ('"The policy threshold is 999 VND." [SRC:D18P2]', False),
    ('“The policy threshold is 999 VND.” [SRC:D18P2]', False),
    ('The policy threshold is 999 VND. [SRC:D18P2]', True),
    ('"The difference is 999 VND." [SRC:D18P2]', True),
    ('Difference: "The policy threshold is 999 VND." [SRC:D18P2]', True),
    ('"The policy threshold is 999 VND." means the difference. [SRC:D18P2]', True),
    ('"The policy threshold is 999 VND." [SRC:D17P2]', True),
    ('"The policy threshold is 999 VND." [SRC:D18P2] [SRC:D17P2]', True),
    ('"The policy threshold is 999 VND."', True),
    ('The delta follows the approved calculation. [SRC:D1LAAAAAAAAAAAAAAAA]', False),
    ('The difference is 1000 VND. [SRC:D1LAAAAAAAAAAAAAAAA]', True),
    ('"The overseas allowance is 1.25 USD." [SRC:D18P2]', False),
])
def test_numeric_claim_requires_complete_independent_source_quote(answer, rejected):
    doc = Document(page_content='The policy threshold is 999 VND. The overseas allowance is 1.25 USD.',
        metadata={'doc_id':18, 'trang_so':2, 'version_no':3})
    assert has_dependent_numeric_claim(answer, ('D18P2',), (doc,)) is rejected


def test_shared_source_cannot_gain_independent_numeric_authority():
    doc = Document(page_content='The policy threshold is 999 VND.', metadata={'doc_id':18, 'trang_so':2})
    assert has_dependent_numeric_claim('"The policy threshold is 999 VND." [SRC:D18P2]', (), (doc,))


def test_same_question_cannot_merge_different_dependency_authority():
    from mech_chatbot.rag.query_dependencies import TextDependency, branch_dependencies
    first = TextDependency('Explain the change', ('a',), 'Approved A', (), ())
    second = TextDependency(first.question, ('b',), 'Approved B', (), ())
    with pytest.raises(ValueError, match='Ambiguous computed dependency'):
        branch_dependencies((first, second), first.question)


def test_incomplete_fixed_result_cannot_become_text_evidence():
    from types import SimpleNamespace
    from mech_chatbot.rag.query_dependencies import computed_dependencies
    branch = SimpleNamespace(question='Explain the change', depends_on=('a',))
    with pytest.raises(ValueError, match='Computed dependency is unavailable'):
        computed_dependencies((branch,), ({'branch_id': 'a', 'data_complete': False},))
