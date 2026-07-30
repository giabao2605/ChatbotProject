from datetime import datetime, timedelta
from os import utime

import pytest

from scripts.ops.backup_system import cleanup_old


def test_backup_cleanup_is_opt_in(tmp_path):
    backup = tmp_path / "existing.bak"
    backup.write_bytes(b"backup")
    old = (datetime.now() - timedelta(days=30)).timestamp()
    utime(backup, (old, old))

    cleanup_old(tmp_path, None)

    assert backup.exists()
    with pytest.raises(ValueError, match="positive integer"):
        cleanup_old(tmp_path, 0)
