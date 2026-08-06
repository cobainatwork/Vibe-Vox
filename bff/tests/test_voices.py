"""音色 CRUD：在 BFF HTTP seam 驗證消費端契約與管理平面行為。

消費端契約 `GET /api/tts/voices` 的形狀見 docs/api/tts.md §3。管理平面的建立、
改名與刪除走 /api/admin/voices。系統不附任何音色，新部署清單為空。

design 建立（zero-shot 後定版）尚未實作：該路徑的可用性未經實測，在 spike 給出
結果前不做。clone 建立不受影響——參考音由使用者上傳，不依賴模型的生成品質。
"""

import io
import wave

from fastapi.testclient import TestClient

from vibe_vox.config import Settings
from vibe_vox.main import create_app

_RATE = 24000


def _client(tmp_path) -> TestClient:
    return TestClient(
        create_app(
            settings=Settings(
                db_path=tmp_path / "t.db",
                voice_dir=tmp_path / "voices",
            )
        )
    )


def _wav_bytes(seconds: float = 5.0) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_RATE)
        w.writeframes(b"\x00\x00" * int(seconds * _RATE))
    return buf.getvalue()


def test_voices_empty_on_fresh_deployment(tmp_path):
    """VoxCPM2 沒有內建語者，音色一律由人建立，故新部署清單為空。"""
    client = _client(tmp_path)

    resp = client.get("/api/tts/voices")

    assert resp.status_code == 200
    assert resp.json() == {"voices": []}


def test_create_clone_voice_appears_in_consumer_list(tmp_path):
    client = _client(tmp_path)

    resp = client.post(
        "/api/admin/voices/clone",
        data={"name": "客戶-中年男性", "language": "zh-TW"},
        files={"ref_audio": ("ref.wav", _wav_bytes(), "audio/wav")},
    )

    assert resp.status_code == 201
    created = resp.json()["data"]
    assert created["type"] == "clone"
    assert created["name"] == "客戶-中年男性"
    assert created["language"] == "zh-TW"

    listed = client.get("/api/tts/voices").json()["voices"]
    assert listed == [
        {
            "id": created["id"],
            "name": "客戶-中年男性",
            "type": "clone",
            "language": "zh-TW",
        }
    ]


def _create_clone(client, name: str = "音色 A") -> dict:
    resp = client.post(
        "/api/admin/voices/clone",
        data={"name": name, "language": "zh-TW"},
        files={"ref_audio": ("ref.wav", _wav_bytes(), "audio/wav")},
    )
    assert resp.status_code == 201
    return resp.json()["data"]


def test_duplicate_name_rejected(tmp_path):
    """name 全域唯一：操作者以名稱辨識音色，重複會讓人選錯。"""
    client = _client(tmp_path)
    _create_clone(client, "重複的名字")

    resp = client.post(
        "/api/admin/voices/clone",
        data={"name": "重複的名字", "language": "zh-TW"},
        files={"ref_audio": ("ref.wav", _wav_bytes(), "audio/wav")},
    )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "VOICE_NAME_TAKEN"
    assert len(client.get("/api/tts/voices").json()["voices"]) == 1


def test_delete_voice_removes_it_from_list(tmp_path):
    """刪除先移除 DB 紀錄使新的合成無法再引用；實體檔留給清理程序回收。"""
    client = _client(tmp_path)
    created = _create_clone(client)

    resp = client.delete(f"/api/admin/voices/{created['id']}")

    assert resp.status_code == 200
    assert client.get("/api/tts/voices").json() == {"voices": []}


def test_delete_unknown_voice_returns_404(tmp_path):
    client = _client(tmp_path)

    resp = client.delete("/api/admin/voices/does-not-exist")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "VOICE_NOT_FOUND"


def test_rename_voice_keeps_id(tmp_path):
    """消費端以 id 綁定音色，改名不得換 id（docs/api/tts.md §3）。"""
    client = _client(tmp_path)
    created = _create_clone(client, "舊名字")

    resp = client.put(f"/api/admin/voices/{created['id']}", json={"name": "新名字"})

    assert resp.status_code == 200
    assert resp.json()["data"]["name"] == "新名字"

    listed = client.get("/api/tts/voices").json()["voices"]
    assert listed[0]["id"] == created["id"]
    assert listed[0]["name"] == "新名字"


def test_rename_to_existing_name_rejected(tmp_path):
    client = _client(tmp_path)
    _create_clone(client, "甲")
    b = _create_clone(client, "乙")

    resp = client.put(f"/api/admin/voices/{b['id']}", json={"name": "甲"})

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "VOICE_NAME_TAKEN"


def test_blank_name_rejected(tmp_path):
    """name 是操作者辨識音色的唯一依據，空白名稱讓清單不可用。"""
    client = _client(tmp_path)

    resp = client.post(
        "/api/admin/voices/clone",
        data={"name": "   ", "language": "zh-TW"},
        files={"ref_audio": ("ref.wav", _wav_bytes(), "audio/wav")},
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_VOICE_NAME"


def test_overlong_name_rejected(tmp_path):
    client = _client(tmp_path)

    resp = client.post(
        "/api/admin/voices/clone",
        data={"name": "名" * 201, "language": "zh-TW"},
        files={"ref_audio": ("ref.wav", _wav_bytes(), "audio/wav")},
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_VOICE_NAME"


def test_orphan_reference_audio_swept_on_startup(tmp_path):
    """刪除音色只移除 DB 列，實體檔由啟動時的清理回收。

    沒有這道清理，delete 就是永久洩漏磁碟——而 repository.delete 的 docstring
    正是承諾了它。
    """
    from vibe_vox.files.cleanup import sweep_orphan_voice_files

    client = _client(tmp_path)
    created = _create_clone(client)
    voice_dir = tmp_path / "voices"
    assert len(list(voice_dir.iterdir())) == 1

    client.delete(f"/api/admin/voices/{created['id']}")
    assert len(list(voice_dir.iterdir())) == 1  # 刪除當下不動實體檔

    removed = sweep_orphan_voice_files(voice_dir, tmp_path / "t.db")

    assert len(removed) == 1
    assert list(voice_dir.iterdir()) == []


def test_sweep_keeps_referenced_audio(tmp_path):
    """清理只掃孤兒。誤刪在用中的參考音等於毀掉音色。"""
    from vibe_vox.files.cleanup import sweep_orphan_voice_files

    client = _client(tmp_path)
    _create_clone(client)
    voice_dir = tmp_path / "voices"

    removed = sweep_orphan_voice_files(voice_dir, tmp_path / "t.db")

    assert removed == []
    assert len(list(voice_dir.iterdir())) == 1
