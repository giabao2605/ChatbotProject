import pytest

from mech_chatbot.rag import interaction_router as router


pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_router(monkeypatch):
    monkeypatch.setenv("SEMANTIC_ROUTER_ENABLED", "true")
    router.set_embedder(None)
    yield
    router.set_embedder(None)


@pytest.mark.parametrize(
    ("route", "expected"),
    [
        (
            router.ROUTE_TECHNICAL,
            {
                "chitchat": False,
                "safety": False,
                "retrieval": True,
                "citation": True,
                "meta": False,
                "skips": False,
            },
        ),
        (
            router.ROUTE_CAPABILITY,
            {
                "chitchat": False,
                "safety": False,
                "retrieval": False,
                "citation": False,
                "meta": True,
                "skips": True,
            },
        ),
        (
            router.ROUTE_SAFETY_BLOCK,
            {
                "chitchat": False,
                "safety": True,
                "retrieval": False,
                "citation": False,
                "meta": False,
                "skips": True,
            },
        ),
        (
            router.ROUTE_CHITCHAT,
            {
                "chitchat": True,
                "safety": False,
                "retrieval": False,
                "citation": False,
                "meta": True,
                "skips": True,
            },
        ),
    ],
)
def test_route_result_exposes_the_complete_routing_contract(route, expected):
    result = router.RouteResult(route, router.LAYER_RULE)

    assert {
        "chitchat": result.is_chitchat(),
        "safety": result.is_safety_block(),
        "retrieval": result.requires_retrieval(),
        "citation": result.requires_source_citation(),
        "meta": result.is_meta(),
        "skips": result.skips_retrieval(),
    } == expected


@pytest.mark.parametrize(
    "embedder",
    [
        lambda text: None,
        lambda text: (_ for _ in ()).throw(RuntimeError("embedding failed")),
        lambda text: ["not-a-number"],
        lambda text: [0.0, 0.0],
    ],
)
def test_semantic_router_fails_closed_for_unusable_embeddings(embedder):
    semantic = router.SemanticRouter(
        embedder,
        prototypes={router.ROUTE_CAPABILITY: ["prototype"]},
        threshold=0.5,
        margin=0.1,
    )

    assert semantic.route_scores("") == []
    assert semantic.classify("question") == (None, 0.0, 0.0)


def test_semantic_router_handles_one_route_and_reuses_prototype_vectors():
    calls = []

    def embed(text):
        calls.append(text)
        return [1.0, 0.0] if text in {"question", "capability"} else [0.0, 1.0]

    semantic = router.SemanticRouter(
        embed,
        prototypes={router.ROUTE_CAPABILITY: ["capability"]},
        threshold=0.9,
        margin=0.1,
    )

    assert semantic.classify("question") == (router.ROUTE_CAPABILITY, 1.0, 0.0)
    assert semantic.classify("question") == (router.ROUTE_CAPABILITY, 1.0, 0.0)
    assert calls.count("capability") == 1


def test_global_embedder_is_reused_and_malformed_llm_results_fail_closed():
    router.set_embedder(lambda text: [1.0, 0.0])

    first = router.classify("ambiguous public question")
    second = router.classify("another ambiguous public question")
    malformed = router.classify(
        "still ambiguous",
        embedder=lambda text: None,
        llm_classifier=lambda text, context: "invalid-result",
    )

    assert first.route in router.ALL_ROUTES
    assert second.route in router.ALL_ROUTES
    assert malformed == router.RouteResult(
        router.DEFAULT_ROUTE, router.LAYER_DEFAULT, confidence=0.0
    )


def test_public_citation_helper_distinguishes_social_and_technical_questions():
    assert router.requires_source_citation("xin chào") is False
    assert router.requires_source_citation("dung sai của trục là bao nhiêu") is True
