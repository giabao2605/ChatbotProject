import pytest

from mech_chatbot.rag.late_interaction import LateInteractionConfig, encode_documents


pytestmark = pytest.mark.unit


def test_adjacent_pooling_normalizes_pairs_and_retains_odd_token():
    vectors = [[1.0, 0.0], [0.0, 1.0], [0.0, 1.0]]

    class Encoder:
        def encode(self, texts, **kwargs):
            return {"colbert_vecs": [vectors]}

    result = encode_documents(["example"], encoder=Encoder(), pooling="adjacent_mean")

    assert result[0][0] == pytest.approx([2 ** -0.5, 2 ** -0.5])
    assert result[0][1] == [0.0, 1.0]
    assert len(result[0]) == 2
    assert vectors == [[1.0, 0.0], [0.0, 1.0], [0.0, 1.0]]


def test_default_encoding_keeps_original_vectors():
    class Encoder:
        def encode(self, texts, **kwargs):
            return {"colbert_vecs": [[[2.0, 0.0], [0.0, 3.0]]]}

    assert encode_documents(["example"], encoder=Encoder()) == [[[2.0, 0.0], [0.0, 3.0]]]


def test_invalid_pooling_rejected_before_encoder_call():
    class Encoder:
        def encode(self, texts, **kwargs):
            pytest.fail("invalid pooling reached encoder")

    with pytest.raises(ValueError, match="unsupported_late_document_pooling"):
        encode_documents(["example"], encoder=Encoder(), pooling="unknown")


def test_pooling_configuration_is_explicit_and_validated():
    assert LateInteractionConfig().document_pooling == "none"
    assert LateInteractionConfig(document_pooling="adjacent_mean").document_pooling == "adjacent_mean"
    with pytest.raises(ValueError, match="unsupported_late_document_pooling"):
        LateInteractionConfig(document_pooling="unknown")
