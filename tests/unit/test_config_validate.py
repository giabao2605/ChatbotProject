"""P0 #6 — Test validate config fail-fast + che secret.

Module config.validate THUAN (chi os/stdlib) -> KHONG can qdrant/sql -> chay nhanh.
Dung dict gia lap thay os.environ de test khong dung cham moi truong that.
"""
import pytest

pytestmark = pytest.mark.unit

from mech_chatbot.config import validate as cfg  # noqa: E402
from mech_chatbot.config.settings import Settings  # noqa: E402


def _full_env():
    """Mot bo cau hinh DAY DU, hop le."""
    return {
        "QDRANT_URL": "http://localhost:6333",
        "QDRANT_API_KEY": "secret-qdrant-key-123",
        "PROXYLLM_API_KEY": "secret-llm-key-456",
        "PROXYLLM_BASE_URL": "http://proxy/v1",
        "EMBEDDING_MODEL": "intfloat/multilingual-e5-large",
        "EMBEDDING_DIM": "1024",
        "SQL_SERVER": "localhost\\SQLEXPRESS",
        "SQL_DATABASE": "Mech_Chatbot_DB",
        "SQL_TRUSTED_CONNECTION": "yes",
        "RAG_SERVICE_TOKEN": "secret-rag-token-789",
    }


class TestValidateConfig:
    def test_full_env_has_no_errors(self):
        errors, _ = cfg.validate_config(_full_env())
        assert errors == []

    def test_missing_qdrant_url_is_error(self):
        env = _full_env()
        del env["QDRANT_URL"]
        errors, _ = cfg.validate_config(env)
        assert any("QDRANT_URL" in e for e in errors)

    def test_missing_all_llm_keys_is_error(self):
        env = _full_env()
        del env["PROXYLLM_API_KEY"]
        errors, _ = cfg.validate_config(env)
        assert any("LLM API key" in e for e in errors)

    def test_missing_llm_base_url_is_error(self):
        env = _full_env()
        del env["PROXYLLM_BASE_URL"]
        errors, _ = cfg.validate_config(env)
        assert any("base URL" in e for e in errors)

    def test_missing_embedding_dim_is_error(self):
        env = _full_env()
        del env["EMBEDDING_DIM"]
        errors, _ = cfg.validate_config(env)
        assert any("EMBEDDING_DIM" in e for e in errors)

    def test_sql_not_trusted_without_password_is_error(self):
        env = _full_env()
        env["SQL_TRUSTED_CONNECTION"] = "no"
        env["SQL_USERNAME"] = "sa"
        # thieu SQL_PASSWORD
        errors, _ = cfg.validate_config(env)
        assert any("SQL_PASSWORD" in e or "SQL_USERNAME" in e for e in errors)

    def test_sql_not_trusted_with_credentials_ok(self):
        env = _full_env()
        env["SQL_TRUSTED_CONNECTION"] = "no"
        env["SQL_USERNAME"] = "sa"
        env["SQL_PASSWORD"] = "P@ssw0rd"
        errors, _ = cfg.validate_config(env)
        assert errors == []

    def test_bad_numeric_int_is_error(self):
        env = _full_env()
        env["EMBEDDING_DIM"] = "khong-phai-so"
        errors, _ = cfg.validate_config(env)
        assert any("EMBEDDING_DIM" in e and "so nguyen" in e for e in errors)

    def test_bad_numeric_float_is_error(self):
        env = _full_env()
        env["GPT_TEMPERATURE"] = "hot"
        errors, _ = cfg.validate_config(env)
        assert any("GPT_TEMPERATURE" in e for e in errors)

    def test_bad_voyage_timeout_is_error(self):
        env = _full_env()
        env["VOYAGE_RERANK_TIMEOUT_SECONDS"] = "too-slow"
        errors, _ = cfg.validate_config(env)
        assert any("VOYAGE_RERANK_TIMEOUT_SECONDS" in e for e in errors)

    def test_can_skip_groups(self):
        # Chi kiem qdrant, bo qua llm/sql/embedding
        env = {"QDRANT_URL": "x", "QDRANT_API_KEY": "y"}
        errors, _ = cfg.validate_config(
            env, require_llm=False, require_sql=False, require_embedding=False
        )
        assert errors == []

    def test_missing_rag_service_token_is_error_when_required(self):
        env = _full_env()
        del env["RAG_SERVICE_TOKEN"]
        errors, _ = cfg.validate_config(env, require_service_auth=True)
        assert any("RAG_SERVICE_TOKEN" in e for e in errors)

    def test_external_ai_local_escape_is_rejected_outside_local_development(self):
        env = _full_env()
        env["APP_ENV"] = "pilot"
        env["EXTERNAL_AI_LOCAL_DEVELOPMENT"] = "true"
        errors, _ = cfg.validate_config(env)
        assert any("EXTERNAL_AI_LOCAL_DEVELOPMENT" in error for error in errors)

    def test_realtime_strict_streaming_is_rejected_until_sentence_verification_exists(self):
        env = _full_env()
        env["STRICT_REALTIME_STREAMING"] = "true"
        errors, _ = cfg.validate_config(env)
        assert any("STRICT_REALTIME_STREAMING" in error for error in errors)


class TestAssertConfigValid:
    def test_raises_on_invalid(self):
        with pytest.raises(cfg.ConfigError):
            cfg.assert_config_valid({}, require_qdrant=True)

    def test_passes_on_valid(self):
        # Khong raise
        cfg.assert_config_valid(_full_env())


class TestSecretMasking:
    def test_mask_hides_value(self):
        masked = cfg.mask_secret("super-secret-token-9999")
        assert "super-secret" not in masked
        assert masked.endswith("9999")

    def test_mask_short_value(self):
        assert cfg.mask_secret("ab") == "****"

    def test_mask_empty(self):
        assert cfg.mask_secret("") == "(trong)"

    def test_summary_never_leaks_secret_value(self):
        env = _full_env()
        env["VOYAGE_API_KEY"] = "secret-voyage-key-999"
        env["APP_SESSION_SECRET"] = "secret-app-session-111"
        env["CHAT_BRIDGE_SECRET"] = "secret-chat-bridge-222"
        summary = cfg.safe_config_summary(env)
        blob = str(summary)
        # Gia tri secret that KHONG duoc xuat hien
        assert "secret-qdrant-key-123" not in blob
        assert "secret-llm-key-456" not in blob
        assert "secret-rag-token-789" not in blob
        assert "secret-voyage-key-999" not in blob
        assert "secret-app-session-111" not in blob
        assert "secret-chat-bridge-222" not in blob
        # Nhung phai bao la da SET
        assert "SET(" in summary["QDRANT_API_KEY"]
        assert "SET(" in summary["VOYAGE_API_KEY"]

    def test_summary_marks_missing_secret(self):
        env = _full_env()
        del env["QDRANT_API_KEY"]
        summary = cfg.safe_config_summary(env)
        assert summary["QDRANT_API_KEY"] == "MISSING"


class TestAppSecurityConfig:
    def test_production_rejects_insecure_settings_without_leaking_secrets(self):
        sentinel = "shared-sensitive-production-value"
        settings = Settings.from_env(
            {
                "APP_ENV": "production",
                "APP_SESSION_SECRET": sentinel,
                "CHAT_BRIDGE_SECRET": sentinel,
                "RAG_SERVICE_TOKEN": sentinel,
                "APP_COOKIE_SECURE": "false",
                "APP_COOKIE_SAMESITE": "none",
            }
        )

        errors = cfg.validate_app_security(settings)
        message = "\n".join(errors)

        assert "APP_SESSION_SECRET" in message
        assert "APP_COOKIE_SECURE" in message
        assert "APP_COOKIE_SAMESITE" in message
        assert "APP_TRUSTED_HOSTS" in message
        assert "EXTERNAL_PROCESSING_POLICY" in message
        assert sentinel not in message

    def test_production_accepts_distinct_explicit_secure_settings(self):
        settings = Settings.from_env(
            {
                "APP_ENV": "production",
                "APP_SESSION_SECRET": "session-secret-with-at-least-32-bytes",
                "CHAT_BRIDGE_SECRET": "bridge-secret",
                "RAG_SERVICE_TOKEN": "rag-secret",
                "APP_COOKIE_SECURE": "true",
                "APP_COOKIE_SAMESITE": "strict",
                "APP_TRUSTED_HOSTS": "app.example.com",
                "EXTERNAL_PROCESSING_POLICY": "internal_only",
            }
        )

        assert cfg.validate_app_security(settings) == []

    def test_production_rejects_short_session_secret(self):
        settings = Settings.from_env(
            {
                "APP_ENV": "production",
                "APP_SESSION_SECRET": "too-short",
                "APP_COOKIE_SECURE": "true",
                "APP_COOKIE_SAMESITE": "strict",
                "APP_TRUSTED_HOSTS": "app.example.com",
                "EXTERNAL_PROCESSING_POLICY": "internal_only",
            }
        )

        assert any(
            "32" in error for error in cfg.validate_app_security(settings)
        )

    def test_production_rejects_blank_trusted_host_allowlist(self):
        settings = Settings.from_env(
            {
                "APP_ENV": "production",
                "APP_SESSION_SECRET": "session-secret-with-at-least-32-bytes",
                "APP_COOKIE_SECURE": "true",
                "APP_COOKIE_SAMESITE": "strict",
                "APP_TRUSTED_HOSTS": "  ",
                "EXTERNAL_PROCESSING_POLICY": "internal_only",
            }
        )

        assert any(
            "APP_TRUSTED_HOSTS" in error
            for error in cfg.validate_app_security(settings)
        )

    def test_production_rejects_wildcard_trusted_hosts(self):
        settings = Settings.from_env(
            {
                "APP_ENV": "production",
                "APP_SESSION_SECRET": "session-secret-with-at-least-32-bytes",
                "APP_COOKIE_SECURE": "true",
                "APP_COOKIE_SAMESITE": "strict",
                "APP_TRUSTED_HOSTS": "*.example.com",
                "EXTERNAL_PROCESSING_POLICY": "internal_only",
            }
        )

        assert any(
            "APP_TRUSTED_HOSTS" in error
            for error in cfg.validate_app_security(settings)
        )

    def test_production_rejects_local_session_secret_fallback(self):
        sentinel = "local-bridge-value-that-must-stay-masked"
        settings = Settings.from_env(
            {
                "APP_ENV": "production",
                "CHAT_BRIDGE_SECRET": sentinel,
                "RAG_SERVICE_TOKEN": "rag-secret",
                "APP_COOKIE_SECURE": "true",
                "APP_COOKIE_SAMESITE": "strict",
                "APP_TRUSTED_HOSTS": "app.example.com",
                "EXTERNAL_PROCESSING_POLICY": "internal_only",
            }
        )

        message = "\n".join(cfg.validate_app_security(settings))

        assert "APP_SESSION_SECRET" in message
        assert sentinel not in message

    def test_local_app_keeps_current_insecure_http_defaults(self):
        assert cfg.validate_app_security(Settings.from_env({})) == []
