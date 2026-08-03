"""#5 ASR 語音轉文字測試 — seam 為 BFF HTTP `POST /api/asr/transcribe`。

真實 AsrClient 接 vLLM 需真模型，不進測試 seam；一律以 StubAsrClient 替身注入，
並以假音檔輸入模組（_FakeIntake）避開 ffmpeg，讓 HTTP seam 測試本機可跑。
"""

from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vibe_qwen.adapters.base import Segment, TranscriptionResult
from vibe_qwen.adapters.stub import StubAsrClient
from vibe_qwen.audio.errors import (
    FileTooLarge,
    TranscodeError,
    TranscodeTimeout,
    UnsupportedAudioFormat,
)
from vibe_qwen.config import Settings
from vibe_qwen.main import create_app


class _FakeIntake:
    """假音檔輸入模組：消耗 chunks、yield 固定 wav path，避開 ffmpeg。"""

    @asynccontextmanager
    async def transcoded(self, chunks, *, sample_rate, channels=1):
        async for _ in chunks:
            pass
        yield Path("fake.wav")


class _RaisingIntake:
    """在 transcode 階段拋出指定 audio 例外，驗證端點的 HTTP 映射。"""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    @asynccontextmanager
    async def transcoded(self, chunks, *, sample_rate, channels=1):
        async for _ in chunks:
            pass
        raise self._exc
        yield  # pragma: no cover — 使函式成為 async generator


def _client(tmp_path, result):
    return TestClient(
        create_app(
            settings=Settings(db_path=tmp_path / "t.db"),
            asr_client=StubAsrClient(result=result),
            audio_intake=_FakeIntake(),
        )
    )


def test_transcribe_returns_consumer_contract_shape(tmp_path):
    result = TranscriptionResult(
        segments=[Segment(Start=0.0, End=1.2, Speaker="A", Content="你好")],
        raw_text="你好",
        transcription_only="你好",
        duration=1.2,
    )
    client = _client(tmp_path, result)

    resp = client.post(
        "/api/asr/transcribe",
        files={"file": ("a.wav", b"RIFF\x00\x00\x00\x00WAVE", "audio/wav")},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["segments"] == [
        {"Start": 0.0, "End": 1.2, "Speaker": "A", "Content": "你好"}
    ]
    assert body["raw_text"] == "你好"
    assert body["transcription_only"] == "你好"
    assert body["duration"] == 1.2
    assert body["applied_context"] == ""
    assert "data" not in body  # 消費端契約不套 {data} 信封


def test_transcribe_applies_enabled_hotword_context(tmp_path):
    result = TranscriptionResult(
        segments=[], raw_text="", transcription_only="", duration=0.0
    )
    stub = StubAsrClient(result=result)
    client = TestClient(
        create_app(
            settings=Settings(db_path=tmp_path / "t.db"),
            asr_client=stub,
            audio_intake=_FakeIntake(),
        )
    )
    client.post("/api/admin/hotwords", json={"term": "台積電"})
    client.post("/api/admin/hotwords", json={"term": "聯發科"})
    disabled = client.post("/api/admin/hotwords", json={"term": "停用詞"}).json()["data"]
    client.patch(
        f"/api/admin/hotwords/{disabled['id']}/enabled", json={"enabled": False}
    )

    resp = client.post(
        "/api/asr/transcribe",
        files={"file": ("a.wav", b"RIFF\x00\x00\x00\x00WAVE", "audio/wav")},
    )

    assert resp.status_code == 200
    applied = resp.json()["applied_context"]
    assert "台積電" in applied
    assert "聯發科" in applied
    assert "停用詞" not in applied  # 停用者不進 context
    assert stub.last_context == applied  # 編譯後的 context 確實傳給 ASR


def test_transcribe_appends_extra_terms_to_enabled(tmp_path):
    result = TranscriptionResult(
        segments=[], raw_text="", transcription_only="", duration=0.0
    )
    client = TestClient(
        create_app(
            settings=Settings(db_path=tmp_path / "t.db"),
            asr_client=StubAsrClient(result=result),
            audio_intake=_FakeIntake(),
        )
    )
    client.post("/api/admin/hotwords", json={"term": "台積電"})

    resp = client.post(
        "/api/asr/transcribe",
        files={"file": ("a.wav", b"RIFF\x00\x00\x00\x00WAVE", "audio/wav")},
        data={"extra_terms": '["王小明", "李美華"]'},
    )

    assert resp.status_code == 200
    applied = resp.json()["applied_context"]
    assert "台積電" in applied  # 啟用中的仍在
    assert "王小明" in applied  # 本次臨時追加
    assert "李美華" in applied


def test_transcribe_override_replaces_enabled_terms(tmp_path):
    result = TranscriptionResult(
        segments=[], raw_text="", transcription_only="", duration=0.0
    )
    client = TestClient(
        create_app(
            settings=Settings(db_path=tmp_path / "t.db"),
            asr_client=StubAsrClient(result=result),
            audio_intake=_FakeIntake(),
        )
    )
    client.post("/api/admin/hotwords", json={"term": "台積電"})

    resp = client.post(
        "/api/asr/transcribe",
        files={"file": ("a.wav", b"RIFF\x00\x00\x00\x00WAVE", "audio/wav")},
        data={"extra_terms": '["只用這個"]', "replace_context": "true"},
    )

    assert resp.status_code == 200
    applied = resp.json()["applied_context"]
    assert "台積電" not in applied  # 啟用集被本次覆寫
    assert "只用這個" in applied


def test_transcribe_rejects_context_over_budget(tmp_path):
    # 伺服器端強制：估算超出預算的 context 直接回 413，不送模型（與 preview 一致）。
    result = TranscriptionResult(
        segments=[], raw_text="", transcription_only="", duration=0.0
    )
    client = TestClient(
        create_app(
            settings=Settings(db_path=tmp_path / "t.db", hotword_context_token_budget=5),
            asr_client=StubAsrClient(result=result),
            audio_intake=_FakeIntake(),
        )
    )
    client.post("/api/admin/hotwords", json={"term": "台積電聯發科鴻海"})

    resp = client.post(
        "/api/asr/transcribe",
        files={"file": ("a.wav", b"RIFF\x00\x00\x00\x00WAVE", "audio/wav")},
    )

    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "CONTEXT_BUDGET_EXCEEDED"


@pytest.mark.parametrize(
    "exc, status, code",
    [
        (FileTooLarge(), 413, "FILE_TOO_LARGE"),
        (UnsupportedAudioFormat(), 400, "UNSUPPORTED_AUDIO_FORMAT"),
        (TranscodeError(), 400, "TRANSCODE_ERROR"),
        (TranscodeTimeout(), 504, "TRANSCODE_TIMEOUT"),
    ],
)
def test_transcribe_maps_audio_errors(tmp_path, exc, status, code):
    client = TestClient(
        create_app(
            settings=Settings(db_path=tmp_path / "t.db"),
            asr_client=StubAsrClient(),
            audio_intake=_RaisingIntake(exc),
        )
    )

    resp = client.post(
        "/api/asr/transcribe",
        files={"file": ("a.wav", b"RIFF\x00\x00\x00\x00WAVE", "audio/wav")},
    )

    assert resp.status_code == status
    assert resp.json()["error"]["code"] == code


def test_transcribe_rejects_malformed_extra_terms(tmp_path):
    # extra_terms 為呼叫端輸入，畸形 JSON 不得讓端點回 500。
    result = TranscriptionResult(
        segments=[], raw_text="", transcription_only="", duration=0.0
    )
    client = TestClient(
        create_app(
            settings=Settings(db_path=tmp_path / "t.db"),
            asr_client=StubAsrClient(result=result),
            audio_intake=_FakeIntake(),
        )
    )

    resp = client.post(
        "/api/asr/transcribe",
        files={"file": ("a.wav", b"RIFF\x00\x00\x00\x00WAVE", "audio/wav")},
        data={"extra_terms": "not json {{{"},
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_EXTRA_TERMS"


def test_transcribe_rejects_non_string_extra_terms(tmp_path):
    # 元素須為字串：非字串應拒絕，而非靜默轉字串注入 context。
    result = TranscriptionResult(
        segments=[], raw_text="", transcription_only="", duration=0.0
    )
    client = TestClient(
        create_app(
            settings=Settings(db_path=tmp_path / "t.db"),
            asr_client=StubAsrClient(result=result),
            audio_intake=_FakeIntake(),
        )
    )

    resp = client.post(
        "/api/asr/transcribe",
        files={"file": ("a.wav", b"RIFF\x00\x00\x00\x00WAVE", "audio/wav")},
        data={"extra_terms": "[123]"},
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_EXTRA_TERMS"
