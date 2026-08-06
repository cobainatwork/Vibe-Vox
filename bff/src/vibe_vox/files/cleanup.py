"""檔案清理：移除孤兒檔，於 BFF 啟動序列執行。

兩種孤兒各有一支：暫存檔依保留期清、音色參考音依「DB 沒有列引用它」清。後者是
刪除音色的必要配套——刪除刻意只移除 DB 列而不動實體檔（避免與進行中的合成競態），
沒有這道清理就是永久洩漏磁碟。
"""

import sqlite3
import time
from os import PathLike
from pathlib import Path


def cleanup_expired_temp_files(
    temp_dir: str | PathLike,
    max_age_seconds: float,
    now: float | None = None,
) -> list[str]:
    """刪除 temp_dir 下 mtime 早於 now - max_age_seconds 的檔案，回傳已刪檔名。"""
    now = time.time() if now is None else now
    base = Path(temp_dir)
    if not base.is_dir():
        return []

    removed: list[str] = []
    for entry in base.iterdir():
        if entry.is_file() and now - entry.stat().st_mtime > max_age_seconds:
            entry.unlink()
            removed.append(entry.name)
    return removed


def sweep_orphan_voice_files(
    voice_dir: str | PathLike, db_path: str | PathLike
) -> list[str]:
    """刪除 voice_dir 下未被任何 Voice 列引用的檔案，回傳已刪檔名。

    **不套保留期**：孤兒的判準是「DB 沒有列引用它」，而 DB 是唯一的真相來源。
    以 mtime 判斷會誤刪剛建立的音色。

    DB 不存在或 voices 表尚未建立時回空清單而非清空目錄——那種狀態下無從判斷誰是
    孤兒，寧可洩漏也不能誤刪在用中的參考音。
    """
    base = Path(voice_dir)
    if not base.is_dir():
        return []

    try:
        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute("SELECT ref_audio_path FROM voices").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []

    referenced = {Path(r[0]).resolve() for r in rows if r[0]}

    removed: list[str] = []
    for entry in base.iterdir():
        if entry.is_file() and entry.resolve() not in referenced:
            entry.unlink()
            removed.append(entry.name)
    return removed
