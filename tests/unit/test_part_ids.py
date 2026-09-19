import pytest

from mech_chatbot.domain.part_ids import canonical_part_id_updates


pytestmark = pytest.mark.unit


def test_canonical_part_id_updates_normalizes_mixed_case_without_mutating_input():
    metadata = {
        "ma_chinh": ["Demo-Tech-001", "DEMO-TECH-001"],
        "ma_lien_quan": "Related-002",
        "title": "Giữ nguyên",
    }

    assert canonical_part_id_updates(metadata) == {
        "ma_chinh": ["DEMO-TECH-001"],
        "ma_lien_quan": ["RELATED-002"],
    }
    assert metadata["ma_chinh"] == ["Demo-Tech-001", "DEMO-TECH-001"]
