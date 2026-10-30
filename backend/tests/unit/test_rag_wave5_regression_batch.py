import pytest

from mech_chatbot.rag import regression

pytestmark = pytest.mark.unit


def test_regression_batch_requires_authenticated_actor():
    with pytest.raises(TypeError):
        regression.run_regression_batch()


def test_regression_batch_requires_configured_execution():
    with pytest.raises(RuntimeError,match='Configured RAG execution'):
        regression.run_regression_batch(actor={'user_id':7,'username':'alice'})
