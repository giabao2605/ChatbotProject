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
            refreshed, _ = security.create_session_token(
                user_id=1, username='test', session_id=payload.session_id, settings=settings, issued_at=199,
            )
            for check_time in (401, 499, 500, 801):
                monkeypatch.setattr(security.time, 'time', lambda: check_time)
                with pytest.raises(HTTPException):
                    security.verify_session_token(refreshed, settings=settings)
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
