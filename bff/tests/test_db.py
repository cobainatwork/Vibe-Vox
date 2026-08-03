"""S3：SQLite 連線套用 WAL 模式與 busy_timeout，緩解併發寫入的 SQLITE_BUSY。"""

from vibe_vox.persistence.db import connect


def test_connection_uses_wal_and_busy_timeout(tmp_path):
    conn = connect(tmp_path / "vibe_vox.db")
    try:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        conn.close()

    assert journal_mode.lower() == "wal"
    assert busy_timeout == 5000
