"""S4：啟動序列清理過期暫存檔，回收硬崩潰／強制重啟遺留的孤兒檔。"""

import os
import time

from fastapi.testclient import TestClient

from vibe_vox.config import Settings
from vibe_vox.files.cleanup import cleanup_expired_temp_files
from vibe_vox.main import create_app


def test_cleanup_removes_expired_files_and_keeps_fresh(tmp_path):
    now = 1_000_000
    old = tmp_path / "orphan.tmp"
    old.write_bytes(b"x")
    os.utime(old, (now - 7200, now - 7200))  # 2 小時前，過期

    fresh = tmp_path / "inflight.tmp"
    fresh.write_bytes(b"y")
    os.utime(fresh, (now - 60, now - 60))  # 1 分鐘前，未過期

    removed = cleanup_expired_temp_files(tmp_path, max_age_seconds=3600, now=now)

    assert old.name in removed
    assert not old.exists()
    assert fresh.exists()


def test_app_startup_triggers_temp_cleanup(tmp_path):
    orphan = tmp_path / "orphan.tmp"
    orphan.write_bytes(b"x")
    os.utime(orphan, (time.time() - 100_000, time.time() - 100_000))  # 遠早於保留期

    app = create_app(settings=Settings(temp_dir=tmp_path, temp_max_age_seconds=3600))
    with TestClient(app):  # 進入 context 觸發 lifespan 啟動序列
        pass

    assert not orphan.exists()
