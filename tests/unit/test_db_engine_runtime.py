from dataclasses import FrozenInstanceError
import importlib
from pathlib import Path
import subprocess
import sys

import pytest

from mech_chatbot.config.settings import SqlSettings


pytestmark = pytest.mark.unit


def _sql_settings(**overrides):
    values = {
        "server": r"localhost\SQLEXPRESS",
        "database": "Mech_Chatbot_DB",
        "driver": "ODBC Driver 17 for SQL Server",
        "username": None,
        "password": None,
        "trusted_connection": True,
    }
    values.update(overrides)
    return SqlSettings(**values)


def test_importing_engine_module_does_not_create_an_engine(monkeypatch):
    import sqlalchemy
    import mech_chatbot.db.engine as engine_module

    calls = []
    original_create_engine = sqlalchemy.create_engine

    def fail_if_called(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("engine creation must happen in composition")

    monkeypatch.setattr(sqlalchemy, "create_engine", fail_if_called)

    try:
        reloaded = importlib.reload(engine_module)
    finally:
        monkeypatch.setattr(sqlalchemy, "create_engine", original_create_engine)
        importlib.reload(engine_module)

    assert calls == []
    assert reloaded.current_repository_engine() is None


def test_importing_engine_module_does_not_bootstrap_project_logging():
    project_root = Path(__file__).resolve().parents[2]
    source_root = project_root / "src"
    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(source_root)!r})\n"
        "import mech_chatbot.db.engine\n"
        "assert 'mech_chatbot.config.logging' not in sys.modules\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_build_conn_str_uses_the_explicit_sql_settings_snapshot():
    from mech_chatbot.db.engine import build_conn_str

    settings = _sql_settings(
        server="sql-host",
        database="knowledge",
        driver="ODBC Driver 18 for SQL Server",
        username="chatbot",
        password="sql-secret",
        trusted_connection=False,
    )

    connection_string = build_conn_str(settings)

    assert "DRIVER=ODBC Driver 18 for SQL Server;" in connection_string
    assert "SERVER=sql-host;" in connection_string
    assert "DATABASE=knowledge;" in connection_string
    assert "UID=chatbot;" in connection_string
    assert "PWD=sql-secret;" in connection_string
    assert "Trusted_Connection" not in connection_string


def test_build_conn_str_rejects_incomplete_sql_auth_without_leaking_secret():
    from mech_chatbot.db.engine import DatabaseConfigurationError, build_conn_str

    settings = _sql_settings(
        username="chatbot",
        password=None,
        trusted_connection=False,
    )

    with pytest.raises(DatabaseConfigurationError) as exc_info:
        build_conn_str(settings)

    message = str(exc_info.value)
    assert message == (
        "SQL authentication requires both username and password when "
        "trusted connection is disabled."
    )
    assert "chatbot" not in message


def test_create_db_engine_uses_explicit_config_and_returns_factory_result(monkeypatch):
    import mech_chatbot.db.engine as engine_module

    created = object()
    calls = []

    def fake_create_engine(url, **kwargs):
        calls.append((url, kwargs))
        return created

    monkeypatch.setattr(engine_module, "create_engine", fake_create_engine)

    result = engine_module.create_db_engine(_sql_settings())

    assert result is created
    assert len(calls) == 1
    url, kwargs = calls[0]
    assert url.startswith("mssql+pyodbc:///?odbc_connect=")
    assert kwargs == {
        "pool_size": 20,
        "max_overflow": 30,
        "pool_pre_ping": True,
        "pool_recycle": 3600,
    }


def test_create_db_engine_sanitizes_provider_failure(monkeypatch):
    import mech_chatbot.db.engine as engine_module

    secret = "sql-secret"

    def fail_create_engine(*_args, **_kwargs):
        raise RuntimeError(f"provider rejected password={secret}")

    monkeypatch.setattr(engine_module, "create_engine", fail_create_engine)

    with pytest.raises(engine_module.DatabaseEngineError) as exc_info:
        engine_module.create_db_engine(
            _sql_settings(
                username="chatbot",
                password=secret,
                trusted_connection=False,
            )
        )

    message = str(exc_info.value)
    assert message == "Unable to initialize the SQL database engine."
    assert secret not in message
    assert exc_info.value.__cause__ is None


def test_database_runtime_is_frozen_and_disposes_its_owned_engine():
    from mech_chatbot.db.engine import DatabaseRuntime

    class FakeEngine:
        def __init__(self):
            self.dispose_calls = 0

        def dispose(self):
            self.dispose_calls += 1

    engine = FakeEngine()
    runtime = DatabaseRuntime(engine=engine)

    runtime.close()

    assert engine.dispose_calls == 1
    with pytest.raises(FrozenInstanceError):
        runtime.engine = object()


def test_build_database_runtime_owns_the_engine_created_from_settings(monkeypatch):
    import mech_chatbot.db.engine as engine_module

    created = object()
    received = []

    def fake_create_db_engine(settings):
        received.append(settings)
        return created

    settings = _sql_settings()
    monkeypatch.setattr(
        engine_module,
        "create_db_engine",
        fake_create_db_engine,
    )

    runtime = engine_module.build_database_runtime(settings)

    assert received == [settings]
    assert runtime.engine is created
    assert engine_module.current_repository_engine() is None


def test_context_bound_engine_delegates_only_inside_composition_scope():
    import mech_chatbot.db.engine as engine_module

    calls = []

    class FakeEngine:
        def connect(self):
            calls.append("connect")
            return "connection"

    with pytest.raises(RuntimeError, match="SQLAlchemy Engine chua san sang"):
        engine_module.engine.connect()

    with engine_module.bind_repository_engine(FakeEngine()):
        assert engine_module.engine.connect() == "connection"

    assert calls == ["connect"]
    assert engine_module.current_repository_engine() is None
