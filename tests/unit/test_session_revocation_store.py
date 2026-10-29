import pytest
from sqlalchemy import create_engine, select

from mech_chatbot.adapters.session_revocations import SqlSessionRevocations

pytestmark = pytest.mark.unit


def test_logout_covers_refresh_issued_before_logout(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from mech_chatbot.api import app_security as security

    settings = SimpleNamespace(session_secret='synthetic', session_ttl_seconds=300)
    engine = create_engine(f"sqlite:///{tmp_path / 'before.db'}")
    store = SqlSessionRevocations(engine)
    store.create_schema()
    try:
        with security.bind_session_revocations(store):
            monkeypatch.setattr(security.time, 'time', lambda: 100)
            old, payload = security.create_session_token(user_id=1, username='test', settings=settings)
            # Logout has already authenticated the old token while refresh runs.
            security.verify_session_token(old, settings=settings)
            monkeypatch.setattr(security.time, 'time', lambda: 199)
            refreshed, renewed = security.create_session_token(
                user_id=1, username='test', session_id=payload.session_id,
                settings=settings, issued_at=199,
            )
            assert renewed.exp == 499
            assert security.verify_session_token(refreshed, settings=settings)
            monkeypatch.setattr(security.time, 'time', lambda: 200)
            security.revoke_session(old, session_id=payload.session_id, expires_at=payload.exp, settings=settings)
            for check_time in (200, 401, 499, 500):
                monkeypatch.setattr(security.time, 'time', lambda: check_time)
                with pytest.raises(HTTPException):
                    security.verify_session_token(refreshed, settings=settings)
    finally:
        engine.dispose()


def test_token_is_expired_at_exact_expiry(monkeypatch):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from mech_chatbot.api import app_security as security

    settings = SimpleNamespace(session_secret='synthetic', session_ttl_seconds=300)
    monkeypatch.setattr(security.time, 'time', lambda: 100)
    token, payload = security.create_session_token(user_id=1, username='test', settings=settings)
    monkeypatch.setattr(security.time, 'time', lambda: payload.exp)
    with pytest.raises(HTTPException) as error:
        security.verify_session_token(token, settings=settings)
    assert error.value.status_code == 401


@pytest.mark.parametrize('issue_time', [199, 200, 800])
def test_logout_lineage_covers_token_from_inflight_refresh(tmp_path, monkeypatch, issue_time):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from mech_chatbot.api import app_security as security

    settings = SimpleNamespace(session_secret='synthetic', session_ttl_seconds=300)
    engine = create_engine(f"sqlite:///{tmp_path / 'race.db'}")
    store = SqlSessionRevocations(engine)
    store.create_schema()
    try:
        with security.bind_session_revocations(store):
            monkeypatch.setattr(security.time, 'time', lambda: 100)
            old, payload = security.create_session_token(user_id=1, username='test', settings=settings)
            # Refresh passed authentication before logout, then issues later.
            security.verify_session_token(old, settings=settings)
            monkeypatch.setattr(security.time, 'time', lambda: 200)
            security.revoke_session(old, session_id=payload.session_id, expires_at=payload.exp, settings=settings)
            monkeypatch.setattr(security.time, 'time', lambda: issue_time)
            if issue_time < 500:
                with pytest.raises(HTTPException) as error:
                    security.create_session_token(
                        user_id=1, username='test', session_id=payload.session_id,
                        settings=settings, issued_at=199,
                    )
                assert error.value.status_code == 401
            else:
                expired, _ = security.create_session_token(
                    user_id=1, username='test', session_id=payload.session_id,
                    settings=settings, issued_at=199,
                )
                with pytest.raises(HTTPException):
                    security.verify_session_token(expired, settings=settings)
    finally:
        engine.dispose()


def test_security_uses_durable_store_after_reconstruction(tmp_path):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from mech_chatbot.api import app_security as security

    settings = SimpleNamespace(session_secret='synthetic', session_ttl_seconds=300)
    url = f"sqlite:///{tmp_path / 'security.db'}"
    engine = create_engine(url)
    store = SqlSessionRevocations(engine)
    store.create_schema()
    token, payload = security.create_session_token(user_id=1, username='test', settings=settings)
    with security.bind_session_revocations(store):
        security.revoke_session(token, session_id=payload.session_id, expires_at=payload.exp)
    engine.dispose()
    engine = create_engine(url)
    try:
        with security.bind_session_revocations(SqlSessionRevocations(engine)):
            with pytest.raises(HTTPException) as error:
                security.verify_session_token(token, settings=settings)
        assert error.value.status_code == 401
    finally:
        engine.dispose()


def test_security_fails_closed_when_revocation_store_is_unavailable():
    from types import SimpleNamespace
    from fastapi import HTTPException
    from mech_chatbot.api import app_security as security

    class BrokenStore:
        def is_revoked(self, keys, now):
            raise RuntimeError('private database detail')

        def revoke(self, keys, expiry, now):
            raise RuntimeError('private database detail')

    settings = SimpleNamespace(session_secret='synthetic', session_ttl_seconds=300)
    token, payload = security.create_session_token(user_id=1, username='test', settings=settings)
    with security.bind_session_revocations(BrokenStore()):
        for action in (
            lambda: security.verify_session_token(token, settings=settings),
            lambda: security.revoke_session(token, expires_at=payload.exp),
        ):
            with pytest.raises(HTTPException) as error:
                action()
            assert error.value.status_code == 503
            assert 'private' not in error.value.detail


def test_revocations_survive_new_engine_and_do_not_store_raw_keys(tmp_path):
    url = f"sqlite:///{tmp_path / 'sessions.db'}"
    engine = create_engine(url)
    store = SqlSessionRevocations(engine)
    store.create_schema()
    store.revoke(['token:synthetic-bearer', 'session:synthetic-lineage'], 200, 100)
    with engine.connect() as connection:
        rows = connection.execute(select(store.table)).fetchall()
    assert all('synthetic' not in str(row) for row in rows)
    engine.dispose()
    reopened = create_engine(url)
    try:
        restored = SqlSessionRevocations(reopened)
        assert restored.is_revoked(['token:synthetic-bearer'], 150)
        assert restored.is_revoked(['session:synthetic-lineage'], 150)
        assert not restored.is_revoked(['session:other'], 150)
        assert not restored.is_revoked(['session:synthetic-lineage'], 200)
    finally:
        reopened.dispose()


def test_later_revocation_cannot_shorten_existing_expiry(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'sessions.db'}")
    try:
        store = SqlSessionRevocations(engine)
        store.create_schema()
        store.revoke(['session:one'], 300, 100)
        store.revoke(['session:one'], 200, 110)
        assert store.is_revoked(['session:one'], 250)
        store.revoke(['session:two'], 400, 301)
        assert not store.is_revoked(['session:one'], 301)
        assert store.is_revoked(['session:two'], 301)
    finally:
        engine.dispose()


def test_password_reset_epoch_survives_store_restart_and_ttl_cleanup(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from mech_chatbot.api import app_security as security

    settings = SimpleNamespace(session_secret="fixture", session_ttl_seconds=300)
    engine = create_engine(f"sqlite:///{tmp_path / 'generation.db'}")
    store = SqlSessionRevocations(engine)
    store.create_schema()
    try:
        with security.bind_session_revocations(store):
            old, old_payload = security.create_session_token(user_id=300, username="fixture", settings=settings)
            security.revoke_user_sessions(300)
            fresh, fresh_payload = security.create_session_token(user_id=300, username="fixture", settings=settings)
        assert fresh_payload.generation > old_payload.generation
        store.revoke(["expired-lineage"], 200, 100)
        store.revoke(["current-lineage"], 700, 500)
        monkeypatch.setattr(security, "_USER_GENERATIONS", {})
        with security.bind_session_revocations(SqlSessionRevocations(engine)):
            with pytest.raises(HTTPException):
                security.verify_session_token(old, settings=settings)
            assert security.verify_session_token(fresh, settings=settings).generation == fresh_payload.generation
            with pytest.raises(HTTPException):
                security.create_session_token(user_id=300, username="fixture", generation=old_payload.generation, settings=settings)
        store.revoke_user(300)
        assert store.user_generation(300) > fresh_payload.generation
        with engine.connect() as connection:
            rows = connection.execute(select(store.table).where(
                store.table.c.KeyHash == store._hash("user-generation:300"),
            )).all()
        assert len(rows) == 1
    finally:
        engine.dispose()


def _password_reset_database(tmp_path, monkeypatch):
    from mech_chatbot.auth import core
    from mech_chatbot.db.repositories import ui_queries
    engine = create_engine(f"sqlite:///{tmp_path / 'password-reset.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE Users (UserID INT, Username TEXT, DisplayName TEXT, Department TEXT, IsActive INT, PasswordHash TEXT)")
        connection.exec_driver_sql("INSERT INTO Users VALUES (500, 'alice', 'Alice', 'IT', 1, 'old-credential')")
    store = SqlSessionRevocations(engine)
    store.create_schema()
    monkeypatch.setattr(core, "engine", engine)
    monkeypatch.setattr(ui_queries, "engine", engine)
    monkeypatch.setattr(core.rate_limit, "is_rate_limited", lambda *_: False)
    monkeypatch.setattr(core.rate_limit, "record_failure", lambda *_: None)
    monkeypatch.setattr(core.rate_limit, "clear_failures", lambda *_: None)
    monkeypatch.setattr(core, "_build_user_profile", lambda _conn, row: {"user_id": row[0], "username": row[1]})
    return engine, store


@pytest.mark.parametrize("reset_at", ["epoch_read", "password_check"])
def test_reset_during_password_verification_cannot_mint_fresh_epoch(tmp_path, monkeypatch, reset_at):
    from types import SimpleNamespace
    from fastapi import HTTPException, Response
    from mech_chatbot.api import app_security as security
    from mech_chatbot.api.routers import operations
    from mech_chatbot.auth import core
    from mech_chatbot.db.repositories import ui_queries

    stale_credential = "old-credential"
    engine, store = _password_reset_database(tmp_path, monkeypatch)
    settings = SimpleNamespace(session_secret="fixture", session_ttl_seconds=300, cookie_secure=False, cookie_samesite="lax")
    monkeypatch.setattr(operations, "authenticate_user", core.authenticate_user)
    def reset():
        ui_queries.update_user_password(500, "new-credential", on_password_updated=lambda connection: store.revoke_user(500, connection=connection))
    def read_epoch(user_id):
        generation = store.user_generation(user_id)
        if reset_at == "epoch_read":
            reset()
        return generation
    def checkpw(candidate, stored):
        if reset_at == "password_check":
            reset()
        return candidate == stored
    monkeypatch.setattr(security, "user_session_generation", read_epoch)
    monkeypatch.setattr(core.bcrypt, "checkpw", checkpw)
    try:
        with security.bind_security_settings(settings), security.bind_session_revocations(store):
            with pytest.raises(HTTPException) as error:
                operations.login(operations.LoginRequest(username="alice", password=stale_credential), Response())
            assert error.value.status_code == 401
        assert store.user_generation(500) > 0
    finally:
        engine.dispose()


def test_password_update_and_epoch_rollback_together(tmp_path, monkeypatch):
    from mech_chatbot.db.repositories import ui_queries
    engine, store = _password_reset_database(tmp_path, monkeypatch)
    def fail_after_revocation(connection):
        store.revoke_user(500, connection=connection)
        raise RuntimeError("forced transaction failure")
    try:
        with pytest.raises(RuntimeError, match="forced transaction failure"):
            ui_queries.update_user_password(500, "new-credential", on_password_updated=fail_after_revocation)
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT PasswordHash FROM Users WHERE UserID = 500").scalar() == "old-credential"
        assert store.user_generation(500) == 0
        assert ui_queries.update_user_password(
            500, "new-credential", on_password_updated=lambda connection: store.revoke_user(500, connection=connection),
        )
        assert store.user_generation(500) > 0
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT PasswordHash FROM Users WHERE UserID = 500").scalar() == "new-credential"
    finally:
        engine.dispose()


def test_password_reset_route_rolls_back_when_revocation_fails(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from mech_chatbot.api import app_security as security
    from mech_chatbot.api.routers import operations

    engine, store = _password_reset_database(tmp_path, monkeypatch)
    class UnavailableRevocations:
        def revoke_user(self, _user_id, *, connection=None):
            raise RuntimeError("unavailable")
    monkeypatch.setattr(operations.bcrypt, "hashpw", lambda *_: b"new-credential")
    try:
        with security.bind_session_revocations(UnavailableRevocations()):
            with pytest.raises(HTTPException) as error:
                operations.user_password(500, {"password": "new-credential"}, {"roles": ["security_admin"]})
            assert error.value.status_code == 503
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT PasswordHash FROM Users WHERE UserID = 500").scalar() == "old-credential"
        assert store.user_generation(500) == 0
    finally:
        engine.dispose()
