"""暫存檔清理：移除超過保留期的孤兒檔，於 BFF 啟動序列執行。"""

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
