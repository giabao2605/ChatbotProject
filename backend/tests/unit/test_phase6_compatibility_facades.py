import hashlib


def _surface_hash(names) -> str:
    payload = "\n".join(sorted(str(name) for name in names))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def test_services_compatibility_surface_stays_exact_during_static_migration():
    import mech_chatbot.services as facade
    from mech_chatbot.services import chat_service

    assert len(facade.__all__) == 137
    assert _surface_hash(facade.__all__) == (
        "3bf4c3445045f05bec10fe268ae8a1c168ece4e99acfe8a1a86739d2bfbba25d"
    )
    assert all(hasattr(facade, name) for name in facade.__all__)
    assert facade.save_chat_history is chat_service.save_chat_history


def test_public_unknown_compatibility_facades_keep_their_reviewed_surfaces():
    import mech_chatbot.db.repository as db_facade
    import mech_chatbot.ingestion.pdf_processor as pdf_facade
    import mech_chatbot.rag.service as rag_facade

    surfaces = (
        (
            db_facade,
            db_facade.__all__,
            206,
            "6246a17b7d6f7b3bed3bcb636e1e624d97ad002fd4b5004f54f402e60225b442",
        ),
        (
            rag_facade,
            [name for name in vars(rag_facade) if not name.startswith("_")],
            84,
            "b9c0c19d3346ccd6bca0f3b56f2e236401373f91fd1d8736fc985a2239cd0a57",
        ),
        (
            pdf_facade,
            [name for name in vars(pdf_facade) if not name.startswith("_")],
            47,
            "75e8a51e3adab687cb2e879efd0869ddf44bd0f42fbcc4f1a675e6837989e87a",
        ),
    )
    for facade, names, expected_count, expected_hash in surfaces:
        assert len(names) == expected_count
        assert _surface_hash(names) == expected_hash
        assert all(hasattr(facade, name) for name in names)

    from mech_chatbot.db.repositories import _shared
    from mech_chatbot.ingestion.pdf import bom as pdf_bom
    from mech_chatbot.rag import pipeline

    assert db_facade.normalize_base_code is _shared.normalize_base_code
    assert db_facade.normalize_base_code(" 9.3.03844 ") == "9.3.03844"
    assert rag_facade.chat_with_rag is pipeline.chat_with_rag
    assert pdf_facade.extract_markdown_tables is pdf_bom.extract_markdown_tables
    assert pdf_facade.extract_markdown_tables("không có bảng") == []
