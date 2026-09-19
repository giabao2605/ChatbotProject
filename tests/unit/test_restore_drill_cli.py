import json
from types import SimpleNamespace

import pytest

from scripts.ops import restore_drill as restore_module


class _ScalarResult:
    def scalar_one_or_none(self):
        return None


class _Connection:
    def execution_options(self, **_kwargs):
        return self

    def execute(self, _statement, _parameters=None):
        return _ScalarResult()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Runtime:
    def __init__(self, *, engine=None, client=None):
        self.engine = engine
        self.client = client
        self.closed = False

    def close(self):
        self.closed = True


def _arguments(tmp_path, *extra):
    return [
        "--sql-backup-path",
        r"D:\Backups\source.bak",
        "--sql-data-dir",
        r"D:\SqlData",
        "--sql-target-database",
        "Mech_Chatbot_DB_RestoreTest_Cli",
        "--qdrant-snapshot-location",
        (
            "http://127.0.0.1:6333/collections/"
            "TaiLieuKyThuat_v2/snapshots/snapshot-1"
        ),
        "--qdrant-snapshot-name",
        "snapshot-1",
        "--qdrant-snapshot-checksum",
        "c" * 64,
        "--qdrant-expected-points",
        "238",
        "--qdrant-target-collection",
        "TaiLieuKyThuat_v2_RestoreTest_Cli",
        "--output",
        str(tmp_path / "restore.json"),
        "--execute",
        *extra,
    ]


@pytest.mark.parametrize(
    ("extra", "expected_wait"),
    (
        ((), 60.0),
        (("--sql-wait-seconds", "12.5"), 12.5),
    ),
)
def test_main_passes_validated_sql_wait_seconds(
    monkeypatch,
    tmp_path,
    extra,
    expected_wait,
):
    settings = SimpleNamespace(
        SQL_DATABASE="Mech_Chatbot_DB",
        QDRANT_COLLECTION="TaiLieuKyThuat_v2",
        QDRANT_API_KEY="test-key",
        QDRANT_URL="http://127.0.0.1:6333",
    )
    connection = _Connection()
    master = _Runtime(
        engine=SimpleNamespace(connect=lambda: connection),
    )
    qdrant = _Runtime(
        client=SimpleNamespace(collection_exists=lambda _name: False),
    )
    captured = {}
    captured_qdrant = {}
    captured_fingerprint = {}
    monkeypatch.setattr(restore_module, "load_settings", lambda: settings)
    monkeypatch.setattr(
        restore_module,
        "clean_git_sha",
        lambda _root: "a" * 40,
    )
    monkeypatch.setattr(
        restore_module,
        "SqlSettings",
        SimpleNamespace(from_settings=lambda _settings: object()),
    )
    monkeypatch.setattr(
        restore_module,
        "QdrantSettings",
        SimpleNamespace(from_settings=lambda _settings: object()),
    )
    monkeypatch.setattr(
        restore_module,
        "replace",
        lambda value, **_changes: value,
    )
    monkeypatch.setattr(
        restore_module,
        "build_database_runtime",
        lambda _settings: master,
    )
    monkeypatch.setattr(
        restore_module,
        "build_qdrant_admin_runtime",
        lambda _settings, **_kwargs: qdrant,
    )

    def restore_sql(_connection, **kwargs):
        captured.update(kwargs)
        return {
            "backup_set_identity_sha256": "b" * 64,
            "restored": True,
        }

    monkeypatch.setattr(restore_module, "restore_sql_backup", restore_sql)
    def restore_qdrant(_client, **kwargs):
        captured_qdrant.update(kwargs)
        return {
            "expected_points": kwargs["expected_points"],
            "target_points": kwargs["expected_points"],
            "restored": True,
        }

    monkeypatch.setattr(
        restore_module,
        "restore_qdrant_snapshot",
        restore_qdrant,
    )
    def build_fingerprint(**kwargs):
        captured_fingerprint.update(kwargs)
        return "d" * 64

    monkeypatch.setattr(
        restore_module,
        "build_restore_snapshot_fingerprint",
        build_fingerprint,
    )

    assert restore_module.main(_arguments(tmp_path, *extra)) == 0
    assert captured["timeout_seconds"] == expected_wait
    assert captured_qdrant["expected_points"] == 238
    assert captured_fingerprint["expected_points"] == 238
    receipt = json.loads((tmp_path / "restore.json").read_text(encoding="utf-8"))
    assert receipt["qdrant_expected_points"] == 238
    assert receipt["qdrant"]["target_points"] == 238
    assert master.closed is True
    assert qdrant.closed is True


@pytest.mark.parametrize("value", ("0", "-1", "nan", "inf"))
def test_main_rejects_invalid_sql_wait_before_mutation(
    monkeypatch,
    tmp_path,
    capsys,
    value,
):
    monkeypatch.setattr(
        restore_module,
        "load_settings",
        lambda: pytest.fail("settings must not load"),
    )

    with pytest.raises(SystemExit) as raised:
        restore_module.main(
            _arguments(
                tmp_path,
                "--sql-wait-seconds",
                value,
            )
        )

    assert raised.value.code == 2
    assert "must be positive and finite" in capsys.readouterr().err
    assert not (tmp_path / "restore.json").exists()


@pytest.mark.parametrize("value", ("0", "-1", "1.5", "nan"))
def test_main_rejects_invalid_qdrant_expected_points_before_mutation(
    monkeypatch,
    tmp_path,
    value,
):
    monkeypatch.setattr(
        restore_module,
        "load_settings",
        lambda: pytest.fail("settings must not load"),
    )

    with pytest.raises(SystemExit) as raised:
        restore_module.main(
            _arguments(
                tmp_path,
                "--qdrant-expected-points",
                value,
            )
        )

    assert raised.value.code == 2
    assert not (tmp_path / "restore.json").exists()


def test_main_requires_qdrant_expected_points_before_mutation(
    monkeypatch,
    tmp_path,
):
    arguments = _arguments(tmp_path)
    option_index = arguments.index("--qdrant-expected-points")
    del arguments[option_index:option_index + 2]
    monkeypatch.setattr(
        restore_module,
        "load_settings",
        lambda: pytest.fail("settings must not load"),
    )

    with pytest.raises(SystemExit) as raised:
        restore_module.main(arguments)

    assert raised.value.code == 2
    assert not (tmp_path / "restore.json").exists()
