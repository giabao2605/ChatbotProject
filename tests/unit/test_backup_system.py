from datetime import datetime, timedelta
from os import utime

import pytest

from scripts.ops.backup_system import _full_backup_options, cleanup_old


def test_express_backup_omits_unsupported_compression():
    assert _full_backup_options(4) == "WITH INIT"
    assert _full_backup_options(3) == "WITH INIT, COMPRESSION"


def test_backup_cleanup_is_opt_in_and_database_scoped(tmp_path):
    backup = tmp_path / "existing.bak"
    backup.write_bytes(b"backup")
    owned = tmp_path / "Mech_Chatbot_DB_full_20260101_000000.bak"
    owned.write_bytes(b"owned")
    foreign = tmp_path / "Other_DB_full_20260101_000000.bak"
    foreign.write_bytes(b"foreign")
    overlapping = (
        tmp_path / "Mech_Chatbot_DB_full_archive_full_20260101_000000.bak"
    )
    overlapping.write_bytes(b"overlapping")
    old = (datetime.now() - timedelta(days=30)).timestamp()
    for path in (backup, owned, foreign, overlapping):
        utime(path, (old, old))

    cleanup_old(tmp_path, None, database="Mech_Chatbot_DB")

    assert backup.exists()
    assert owned.exists()
    cleanup_old(tmp_path, 14, database="Mech_Chatbot_DB")
    assert not owned.exists()
    assert foreign.exists()
    assert overlapping.exists()
    assert backup.exists()
    with pytest.raises(ValueError, match="positive integer"):
        cleanup_old(tmp_path, 0, database="Mech_Chatbot_DB")
