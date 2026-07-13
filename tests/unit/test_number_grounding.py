import pytest

from mech_chatbot.rag.evidence_gate import find_unsupported_numbers, has_unsupported_numbers


pytestmark = pytest.mark.unit


@pytest.mark.parametrize("source,answer", [("1,500", "1500"), ("1.500", "1500"), ("12,50", "12.5")])
def test_number_grounding_accepts_equivalent_formatting(source, answer):
    assert find_unsupported_numbers(answer, source, "Chi phí bao nhiêu?", strict_mode=True) == []
    assert has_unsupported_numbers(answer, source, "Chi phí bao nhiêu?", strict_mode=True) is False


def test_number_grounding_returns_positioned_violations():
    violations = find_unsupported_numbers(
        "Chi phí là 2500 USD.",
        "Chi phí được duyệt là 1500 USD.",
        "Chi phí bao nhiêu?",
        strict_mode=True,
    )

    assert [(item.raw, item.normalized, item.start, item.end) for item in violations] == [
        ("2500", "2500", 11, 15)
    ]
