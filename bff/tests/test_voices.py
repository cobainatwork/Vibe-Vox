"""音色 CRUD：在 BFF HTTP seam 驗證消費端契約與管理平面行為。

消費端契約 `GET /api/tts/voices` 的形狀見 docs/api/tts.md §3。管理平面的建立、
改名與刪除走 /api/admin/voices。系統不附任何音色，新部署清單為空。

design 建立（zero-shot 後定版）尚未實作：該路徑的可用性未經實測，在 spike 給出
結果前不做。clone 建立不受影響——參考音由使用者上傳，不依賴模型的生成品質。
"""

import io
import shutil
import subprocess
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vibe_vox.config import Settings
from vibe_vox.main import create_app

_RATE = 24000

need_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")


def _client(tmp_path, **settings_kw) -> TestClient:
    # temp_dir 也指向 tmp_path：參考音先落暫存區再搬進音色目錄，驗證失敗時該不留殘檔，
    # 而預設值（var/tmp）在 repo 內，測試無從斷言自己清乾淨了。
    return TestClient(
        create_app(
            settings=Settings(
                db_path=tmp_path / "t.db",
                voice_dir=tmp_path / "voices",
                temp_dir=tmp_path / "tmp",
                **settings_kw,
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


def _admin_list(client) -> list[dict]:
    resp = client.get("/api/admin/voices")
    assert resp.status_code == 200
    return resp.json()["data"]


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


def _assert_nothing_landed(client, tmp_path):
    """不合格的參考音既不留檔也不留 DB 列。

    只斷言「回了 400」不夠：落地在 move 之後、DB 寫入在其後，任一步的清理漏掉就是
    每次失敗的上傳都在磁碟留一份，而那條路徑沒有任何錯誤訊號。
    """
    assert client.get("/api/tts/voices").json() == {"voices": []}
    for d in (tmp_path / "voices", tmp_path / "tmp"):
        assert not d.exists() or list(d.iterdir()) == []


def test_overlong_reference_audio_rejected_at_creation(tmp_path):
    """參考音時長超界在建立時擋下，不是等每次合成才失敗。

    模型端強制 1.0–30.0 秒，超界時回的是 ValueError 的文字而非音訊，在合成路徑表現為
    502 TTS_UNAVAILABLE——而契約 §6 把該碼標為可重試，消費端會依契約退避重試一個永久
    失敗。操作者那邊更糟：他拿不到「參考音太長」這個唯一有用的訊息。
    """
    client = _client(tmp_path)

    resp = client.post(
        "/api/admin/voices/clone",
        data={"name": "太長的參考音", "language": "zh-TW"},
        files={"ref_audio": ("ref.wav", _wav_bytes(40.0), "audio/wav")},
    )

    assert resp.status_code == 400
    body = resp.json()["error"]
    assert body["code"] == "INVALID_REF_AUDIO"
    # 訊息要帶實際值與範圍，否則操作者只能猜要剪到多短。
    assert "40" in body["message"] and "30" in body["message"]
    _assert_nothing_landed(client, tmp_path)


def test_too_short_reference_audio_rejected_at_creation(tmp_path):
    """下界同樣是模型端的硬限制（1.0 秒），不足一樣拿不到音訊。"""
    client = _client(tmp_path)

    resp = client.post(
        "/api/admin/voices/clone",
        data={"name": "太短的參考音", "language": "zh-TW"},
        files={"ref_audio": ("ref.wav", _wav_bytes(0.4), "audio/wav")},
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_REF_AUDIO"
    _assert_nothing_landed(client, tmp_path)


def test_unmeasurable_reference_audio_is_rejected_not_admitted(tmp_path):
    """量不到時長要當成驗證失敗，不能放行。

    容器嗅探只讀檔頭 magic，一個 RIFF/WAVE 開頭的壞檔照樣通過。ASR 側的
    `_audio_duration` 量不到時回退 1.0（那條路徑寧可讓辨識繼續），驗證路徑沿用那個
    回退就等於把 0 秒與 40 秒的檔案都當成合格的 1 秒。
    """
    client = _client(tmp_path)
    broken_wav = b"RIFF\x24\x00\x00\x00WAVE" + b"\x00" * 64

    resp = client.post(
        "/api/admin/voices/clone",
        data={"name": "壞掉的參考音", "language": "zh-TW"},
        files={"ref_audio": ("ref.wav", broken_wav, "audio/wav")},
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_REF_AUDIO"
    _assert_nothing_landed(client, tmp_path)


def test_reference_audio_size_limit_is_decoupled_from_asr_uploads(tmp_path):
    """參考音的大小上限與 ASR 上傳分開。

    兩者共用 audio_max_bytes（200 MiB）時，一個 200 MiB 的參考音會讓**每一次合成**在
    event loop 內同步讀完並編出 267 MiB 的 base64 字串，期間整個 BFF（含 /api/health
    與所有 ASR 請求）停住。30 秒的時長上限容不下那種檔案，故上限本就該小得多。
    """
    client = _client(tmp_path, voice_ref_audio_max_bytes=4096)

    resp = client.post(
        "/api/admin/voices/clone",
        data={"name": "太大的參考音", "language": "zh-TW"},
        files={"ref_audio": ("ref.wav", _wav_bytes(5.0), "audio/wav")},
    )

    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "FILE_TOO_LARGE"
    _assert_nothing_landed(client, tmp_path)


@need_ffmpeg
def test_non_wav_reference_audio_is_measured_too(tmp_path):
    """六種容器都要量得到時長（需 ffmpeg，本機無則 skip、CI 跑）。

    只有 wav 能用 stdlib 讀時長，其餘要起 ffprobe。若非 wav 一律量不到，那條路徑等於
    只收 wav——而 sniff 允許六種容器，操作者上傳 mp3 會拿到「不是可解碼的音訊」。
    """
    src = tmp_path / "ref.flac"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=5", "-y", str(src)],
        check=True,
    )
    client = _client(tmp_path)

    resp = client.post(
        "/api/admin/voices/clone",
        data={"name": "flac 參考音", "language": "zh-TW"},
        files={"ref_audio": ("ref.flac", src.read_bytes(), "audio/flac")},
    )

    assert resp.status_code == 201

    # 同一條 ffprobe 路徑要能認出超界的非 wav 檔，否則上界只對 wav 生效。
    long_src = tmp_path / "long.flac"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=40", "-y", str(long_src)],
        check=True,
    )

    resp = client.post(
        "/api/admin/voices/clone",
        data={"name": "太長的 flac", "language": "zh-TW"},
        files={"ref_audio": ("long.flac", long_src.read_bytes(), "audio/flac")},
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_REF_AUDIO"


def test_reference_audio_within_bounds_is_accepted(tmp_path):
    """邊界內的參考音照常建立——驗證不能把正常的上傳一起擋掉。"""
    client = _client(tmp_path)

    for seconds in (1.0, 30.0):
        resp = client.post(
            "/api/admin/voices/clone",
            data={"name": f"{seconds} 秒", "language": "zh-TW"},
            files={"ref_audio": ("ref.wav", _wav_bytes(seconds), "audio/wav")},
        )
        assert resp.status_code == 201, seconds


def test_admin_list_marks_voices_whose_reference_audio_is_unusable(tmp_path):
    """既有音色的不可用要在音色管理看得出來。

    建立時的驗證只對新音色生效。本票之前建立的音色未經任何檢查，而參考音也可能在建立
    之後才失效（DB 還原、volume 換掛、人工刪檔）。清單是操作者唯一看得到音色的地方——
    不標出來的話他只會看到某個音色試聽失敗，而錯誤訊息出現在別的畫面上。
    """
    client = _client(tmp_path)
    good = _create_clone(client, "正常音色")
    overlong = _create_clone(client, "超界音色")
    missing = _create_clone(client, "檔案遺失的音色")

    paths = {v["id"]: Path(v["ref_audio_path"]) for v in _admin_list(client)}
    paths[overlong["id"]].write_bytes(_wav_bytes(40.0))  # 模擬未經驗證就建立的超界音色
    paths[missing["id"]].unlink()

    listed = {v["id"]: v for v in _admin_list(client)}

    assert listed[good["id"]]["unusable_reason"] is None
    assert "40" in listed[overlong["id"]]["unusable_reason"]
    assert listed[missing["id"]]["unusable_reason"] is not None


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
