"""Hotword 持久化（SQLite）。id 為伺服器生成 UUID，timestamps 由 DB 生成。

管理平面看完整形狀 {id, term, note, enabled, created_at, updated_at}；消費端
契約僅取 {id, word} 子集（word 即 term），映射於 api 層。
"""

import sqlite3
from contextlib import contextmanager
from os import PathLike
from pathlib import Path
from uuid import uuid4

from vibe_vox.hotword_text import clean_term
from vibe_vox.persistence.db import connect


class HotwordNotFound(Exception):
    """指定 id 的 Hotword 不存在。"""


class HotwordRepository:
    def __init__(self, db_path: str | PathLike) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @contextmanager
    def _conn(self):
        conn = connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS hotwords (
                    id         TEXT PRIMARY KEY,
                    term       TEXT NOT NULL,
                    note       TEXT,
                    enabled    INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )

    def create(self, term: str, note: str | None = None) -> dict:
        term = clean_term(term)
        hid = str(uuid4())
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO hotwords (id, term, note) VALUES (?, ?, ?)",
                (hid, term, note),
            )
        created = self.get(hid)
        assert created is not None  # 剛插入，必存在
        return created

    def upsert_many(self, rows: list[dict], batch_size: int = 500) -> tuple[int, int]:
        """以 term 為自然鍵批次 upsert，回 (created, updated)。

        單一連線分批 commit：避免逐列開關連線的開銷，也避免整檔單一大交易
        長時間獨佔寫鎖引發全系統 SQLITE_BUSY（spec 匯入分批交易）。
        """
        created = updated = 0
        with self._conn() as conn:
            for i, row in enumerate(rows, start=1):
                term = clean_term(row["term"])
                existing = conn.execute(
                    "SELECT id FROM hotwords WHERE term = ?", (term,)
                ).fetchone()
                enabled = 1 if row["enabled"] else 0
                if existing is not None:
                    conn.execute(
                        "UPDATE hotwords SET note = ?, enabled = ?, "
                        "updated_at = datetime('now') WHERE term = ?",
                        (row["note"], enabled, term),
                    )
                    updated += 1
                else:
                    conn.execute(
                        "INSERT INTO hotwords (id, term, note, enabled) VALUES (?, ?, ?, ?)",
                        (str(uuid4()), term, row["note"], enabled),
                    )
                    created += 1
                if i % batch_size == 0:
                    conn.commit()
        return created, updated

    def get(self, hid: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM hotwords WHERE id = ?", (hid,)).fetchone()
        return _to_dict(row) if row else None

    def update(self, hid: str, term: str, note: str | None = None) -> dict | None:
        term = clean_term(term)
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE hotwords SET term = ?, note = ?, updated_at = datetime('now') "
                "WHERE id = ?",
                (term, note, hid),
            )
            if cur.rowcount == 0:
                return None
        return self.get(hid)

    def delete(self, hid: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM hotwords WHERE id = ?", (hid,))
            return cur.rowcount > 0

    def set_enabled(self, hid: str, enabled: bool) -> dict | None:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE hotwords SET enabled = ?, updated_at = datetime('now') WHERE id = ?",
                (1 if enabled else 0, hid),
            )
            if cur.rowcount == 0:
                return None
        return self.get(hid)

    def list_enabled(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM hotwords WHERE enabled = 1 ORDER BY created_at DESC, rowid DESC"
            ).fetchall()
        return [_to_dict(r) for r in rows]

    def list_all(self, query: str | None = None) -> list[dict]:
        sql = "SELECT * FROM hotwords"
        params: tuple = ()
        if query:
            like = f"%{query}%"
            sql += " WHERE term LIKE ? OR note LIKE ?"
            params = (like, like)
        sql += " ORDER BY created_at DESC, rowid DESC"
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_to_dict(r) for r in rows]


def _to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "term": row["term"],
        "note": row["note"],
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
