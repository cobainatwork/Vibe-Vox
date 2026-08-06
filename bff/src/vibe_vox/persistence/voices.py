"""Voice 持久化（SQLite）。id 為伺服器生成 UUID，timestamps 由 DB 生成。

音色分兩型：clone（上傳參考音）與 design（文字描述經 zero-shot 生成後定版）。
兩型都有 ref_audio_path——design 的參考音是定版擷取的生成音，故重播路徑與
clone 相同（ADR-0002）。VoxCPM2 沒有內建語者，本表在新部署為空。

**ref_audio_path 是音色身分的唯一錨點。** design 音色若在合成時才從描述重生，
同一段對話會逐句換人，故定版不是效能優化而是功能上的必要條件。

不存 seed：可重現生成的機制尚未在本專案的實際部署路徑上驗證過，存一個沒有已知
用途的欄位只是把未驗證的假設寫進資料模型。要加等驗證過再加。

參考音檔存於檔案系統，本表只存 metadata（spec 持久化決策）。落地檔名一律為
伺服器生成的 UUID，不由 name 推導。
"""

import sqlite3
from contextlib import contextmanager
from os import PathLike
from pathlib import Path
from uuid import uuid4

from vibe_vox.persistence.db import connect


class VoiceNotFound(Exception):
    """指定 id 的 Voice 不存在。"""


class VoiceNameTaken(Exception):
    """name 已被其他 Voice 使用（name 全域唯一）。"""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


class VoiceRepository:
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
                CREATE TABLE IF NOT EXISTS voices (
                    id             TEXT PRIMARY KEY,
                    name           TEXT NOT NULL UNIQUE,
                    type           TEXT NOT NULL CHECK (type IN ('clone', 'design')),
                    language       TEXT NOT NULL,
                    ref_audio_path TEXT NOT NULL,
                    ref_text       TEXT,
                    instruct       TEXT,
                    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )

    def list(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM voices ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    def get(self, vid: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM voices WHERE id = ?", (vid,)).fetchone()
        return dict(row) if row else None

    def create(
        self,
        *,
        name: str,
        type: str,
        language: str,
        ref_audio_path: Path,
        ref_text: str | None = None,
        instruct: str | None = None,
    ) -> dict:
        vid = str(uuid4())
        try:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO voices
                        (id, name, type, language, ref_audio_path, ref_text, instruct)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (vid, name, type, language, str(ref_audio_path), ref_text, instruct),
                )
        except sqlite3.IntegrityError as exc:
            raise VoiceNameTaken(name) from exc
        created = self.get(vid)
        assert created is not None  # 剛插入，必存在
        return created

    def delete(self, vid: str) -> dict:
        """移除 DB 紀錄並回傳被刪的列，使新的合成無法再引用該音色。

        **不刪實體參考音檔**：進行中的合成在請求起始就解析了檔案，當下 rm 會讓它
        崩潰。實體檔留給清理程序於寬限期後回收（spec Voice 音色段）。回傳被刪的列
        是為了讓呼叫端知道該回收哪個檔。
        """
        existing = self.get(vid)
        if existing is None:
            raise VoiceNotFound(vid)
        with self._conn() as conn:
            conn.execute("DELETE FROM voices WHERE id = ?", (vid,))
        return existing

    def rename(self, vid: str, name: str) -> dict:
        if self.get(vid) is None:
            raise VoiceNotFound(vid)
        try:
            with self._conn() as conn:
                conn.execute(
                    "UPDATE voices SET name = ?, updated_at = datetime('now') WHERE id = ?",
                    (name, vid),
                )
        except sqlite3.IntegrityError as exc:
            raise VoiceNameTaken(name) from exc
        renamed = self.get(vid)
        assert renamed is not None
        return renamed
