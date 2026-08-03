"""SQLite 連線工廠：啟用 WAL 與 busy_timeout（spec 持久化決策）。"""

import sqlite3
from os import PathLike

BUSY_TIMEOUT_MS = 5000


def connect(db_path: str | PathLike) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return conn
