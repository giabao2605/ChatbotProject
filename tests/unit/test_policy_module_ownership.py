from __future__ import annotations

import ast
from pathlib import Path

import pytest

from mech_chatbot.domain import number_normalization as domain_numbers
from mech_chatbot.governance import feature_activation as governance_activation
from mech_chatbot.rag import feature_activation as rag_activation
from mech_chatbot.rag import number_normalization as rag_numbers


pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src" / "mech_chatbot"


def _imports(relative_path: str) -> set[str]:
    path = SOURCE_ROOT / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }


def test_rag_policy_modules_preserve_the_legacy_interfaces():
    assert rag_activation.__all__ == governance_activation.__all__
    assert all(
        getattr(rag_activation, name) is getattr(governance_activation, name)
        for name in governance_activation.__all__
    )
    assert rag_numbers.__all__ == domain_numbers.__all__
    assert all(
        getattr(rag_numbers, name) is getattr(domain_numbers, name)
        for name in domain_numbers.__all__
    )


@pytest.mark.parametrize(
    ("relative_path", "owner"),
    [
        ("evaluation/grounded_math.py", "mech_chatbot.domain.number_normalization"),
        ("evaluation/grounding.py", "mech_chatbot.domain.number_normalization"),
        ("evaluation/integrated_hardening.py", "mech_chatbot.governance.feature_activation"),
        ("evaluation/milestone_decisions.py", "mech_chatbot.governance.feature_activation"),
    ],
)
def test_evaluation_policy_dependencies_point_to_neutral_owners(
    relative_path: str,
    owner: str,
):
    imports = _imports(relative_path)

    assert owner in imports
    assert all(not dependency.startswith("mech_chatbot.rag") for dependency in imports)
