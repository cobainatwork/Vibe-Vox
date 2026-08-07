"""#5 ASR 語音轉文字測試 — seam 為 BFF HTTP `POST /api/asr/transcribe`。

真實 AsrClient 接 vLLM 需真模型，不進測試 seam；一律以 StubAsrClient 替身注入，
並以假音檔輸入模組（_FakeIntake）避開 ffmpeg，讓 HTTP seam 測試本機可跑。
"""

import asyncio
import logging
import wave
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vibe_vox.adapters.aligner import HttpAlignerClient
from vibe_vox.adapters.base import (
    AsrTimeout,
    AsrUnavailable,
    Omission,
    Segment,
    SegmentAlignment,
    TranscriptionResult,
    Word,
)
from vibe_vox.adapters.stub import StubAlignerClient, StubAsrClient
from vibe_vox.adapters.vllm_asr import VllmAsrClient
from vibe_vox.audio.errors import (
    FileTooLarge,
    TranscodeError,
    TranscodeTimeout,
    UnsupportedAudioFormat,
)
from vibe_vox.config import Settings
from vibe_vox.main import create_app


class _FakeIntake:
    """假音檔輸入模組：消耗 chunks、yield 真 wav path，避開 ffmpeg。

    產出真檔而非不存在的路徑：端點需讀音檔實際長度（`alignment.audio_duration`），
    而該值不能以 Segment End 最大值代替（docs/api/asr.md §4.2）。
    """

    def __init__(self, directory: Path, seconds: float = 2.0) -> None:
        self._directory = directory
        self._seconds = seconds

    @asynccontextmanager
    async def transcoded(self, chunks, *, sample_rate, channels=1):
        async for _ in chunks:
            pass
        path = self._directory / "fake.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(channels)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(b"\x00\x00" * int(self._seconds * sample_rate))
        try:
            yield path
        finally:
            path.unlink(missing_ok=True)


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


class _DegradingAligner:
    """對齊服務不可用的替身，驗證第二層降級。

    回全段空結果配同一句原因，形狀與真 client 在服務掛掉時的回傳一致——降級由
    `AlignerClient.align` 保證，故替身**不拋例外**，端點層也就沒有東西可攔。
    """

    def __init__(self, detail: str) -> None:
        self._omission = Omission("batch_failed", detail)

    async def health(self) -> bool:
        return False

    async def align(self, audio, segments):
        return [
            SegmentAlignment(words=[], bounds=(s.Start, s.End), omission=self._omission)
            for s in segments
        ]


def _client(tmp_path, result, aligner_client=None):
    return TestClient(
        create_app(
            settings=Settings(db_path=tmp_path / "t.db"),
            asr_client=StubAsrClient(result=result),
            aligner_client=aligner_client or StubAlignerClient(),
            audio_intake=_FakeIntake(tmp_path),
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
    # 向後相容：既有四欄形狀與值不變，對齊只加欄位（ADR-0004 契約擴充）。
    segment = body["segments"][0]
    assert segment["Start"] == 0.0  # 未對齊時維持切點語義
    assert segment["End"] == 1.2
    assert segment["Speaker"] == "A"
    assert segment["Content"] == "你好"
    assert body["raw_text"] == "你好"
    assert body["transcription_only"] == "你好"
    assert body["duration"] == 1.2
    assert body["applied_context"] == ""
    assert "data" not in body  # 消費端契約不套 {data} 信封


def test_transcribe_returns_word_level_alignment(tmp_path):
    # 段長 0.9 秒配 0.48 秒的對齊跨距：VibeVoice 的窮盡連續切分下，正常段的跨距
    # 應接近段長，過短會被合理性檢查攔下（見 test_alignment.py）。
    result = TranscriptionResult(
        segments=[Segment(Start=0.0, End=0.9, Speaker="A", Content="你好")],
        raw_text="你好",
        transcription_only="你好",
        duration=0.9,
    )
    aligned_words = [
        SegmentAlignment(
            words=[
                Word(Text="你", Start=0.42, End=0.58),
                Word(Text="好", Start=0.58, End=0.90),
            ],
            bounds=(0.0, 1.4),  # 段界 0.0–0.9 加 buffer，右側被 2.0 秒的音檔涵蓋
        )
    ]
    client = _client(
        tmp_path, result, aligner_client=StubAlignerClient(result=aligned_words)
    )

    resp = client.post(
        "/api/asr/transcribe",
        files={"file": ("a.wav", b"RIFF\x00\x00\x00\x00WAVE", "audio/wav")},
    )

    assert resp.status_code == 200
    body = resp.json()
    segment = body["segments"][0]
    assert segment["aligned"] is True
    assert segment["words"] == [
        {"Text": "你", "Start": 0.42, "End": 0.58},
        {"Text": "好", "Start": 0.58, "End": 0.90},
    ]
    # 段界改為首字 Start／末字 End；原切點 0.0／1.2 已被取代。
    assert segment["Start"] == 0.42
    assert segment["End"] == 0.90
    assert body["duration"] == 0.90  # 隨段界重算
    assert body["alignment"] == {
        "audio_duration": 2.0,  # _FakeIntake 產生的音檔實際長度
        "speech_start": 0.42,
        "speech_end": 0.90,
        "aligned_duration": 0.48,
    }


def test_batch_size_setting_reaches_the_real_aligner_client(tmp_path, monkeypatch):
    # 設定若沒接到 client 上，env var 會**靜默無效**：client 的預設值恰好等於設定的預設
    # 值，故漏接不表現為任何症狀，直到有人真的去設它才會撞 400（#35 那類失效）。
    #
    # 斷言私有屬性是刻意的取捨：client 沒有對外揭露該值的介面，而唯一的行為性 seam
    # （送出的請求數）需要注入 transport，本工廠不接受。漏接的代價大於這點耦合。
    monkeypatch.setenv("VIBE_VOX_ALIGNER_MAX_BATCH_ITEMS", "8")
    app = create_app(settings=Settings(db_path=tmp_path / "t.db"))

    assert app.state.aligner_client._max_batch_items == 8


def test_transcribe_logs_the_service_reason_without_repeating_it_per_segment(
    tmp_path, caplog
):
    # #36 的實測 log 是一條服務層級的訊息後面跟著 63 條完全相同的「字級清單為空」，
    # 真正的原因被推出畫面，診斷時要往上翻 63 行才看得到。原因隨結果過 seam 後，同因的
    # 段落合成一條，端點層不再需要知道「服務整體失敗」這個事實（#37）。
    result = TranscriptionResult(
        segments=[
            Segment(Start=0.0, End=1.0, Speaker="0", Content="你好"),
            Segment(Start=1.0, End=2.0, Speaker="0", Content="再見"),
        ],
        raw_text="你好再見",
        transcription_only="你好再見",
        duration=2.0,
    )
    client = _client(
        tmp_path,
        result,
        aligner_client=_DegradingAligner(
            "第 1／1 批對齊失敗：HTTP 400 BATCH_TOO_LARGE："
            "單次 63 段超過上限 32 段，請分批送。"
        ),
    )

    with caplog.at_level(logging.WARNING):
        resp = client.post(
            "/api/asr/transcribe",
            files={"file": ("a.wav", b"RIFF\x00\x00\x00\x00WAVE", "audio/wav")},
        )

    assert resp.status_code == 200
    assert "BATCH_TOO_LARGE" in caplog.text
    assert "字級清單為空" not in caplog.text


@pytest.mark.parametrize(
    "reason", ["HTTP 503 ALIGNER_NOT_READY：尚未就緒。", "逾時 60 秒未回應"]
)
def test_transcribe_still_returns_transcript_when_aligner_fails(tmp_path, reason):
    # 第二層降級（ADR-0004）：逐字稿有獨立價值，不因評分這項附加功能失效而一併
    # 不可得。故對齊服務掛掉或逾時**不得**映射成 502／504。
    result = TranscriptionResult(
        segments=[Segment(Start=0.0, End=1.2, Speaker="A", Content="你好")],
        raw_text="你好",
        transcription_only="你好",
        duration=1.2,
    )
    client = _client(tmp_path, result, aligner_client=_DegradingAligner(reason))

    resp = client.post(
        "/api/asr/transcribe",
        files={"file": ("a.wav", b"RIFF\x00\x00\x00\x00WAVE", "audio/wav")},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["transcription_only"] == "你好"  # 逐字稿照常
    segment = body["segments"][0]
    assert segment["aligned"] is False
    assert segment["words"] == []
    assert (segment["Start"], segment["End"]) == (0.0, 1.2)  # 退回切點語義
    assert body["alignment"] == {
        "audio_duration": 2.0,
        "speech_start": None,
        "speech_end": None,
        "aligned_duration": 0.0,
    }


def test_transcribe_returns_complete_alignment_structure_without_speech(tmp_path):
    # 學員全程未發話：alignment 結構完整回傳、值為 null 或 0，不報錯不省略欄位。
    result = TranscriptionResult(
        segments=[], raw_text="", transcription_only="", duration=0.0
    )
    client = _client(tmp_path, result)

    resp = client.post(
        "/api/asr/transcribe",
        files={"file": ("a.wav", b"RIFF\x00\x00\x00\x00WAVE", "audio/wav")},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["segments"] == []
    assert body["alignment"] == {
        "audio_duration": 2.0,
        "speech_start": None,
        "speech_end": None,
        "aligned_duration": 0.0,
    }


def test_transcribe_applies_enabled_hotword_context(tmp_path):
    result = TranscriptionResult(
        segments=[], raw_text="", transcription_only="", duration=0.0
    )
    stub = StubAsrClient(result=result)
    client = TestClient(
        create_app(
            settings=Settings(db_path=tmp_path / "t.db"),
            asr_client=stub,
            aligner_client=StubAlignerClient(),
            audio_intake=_FakeIntake(tmp_path),
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
            aligner_client=StubAlignerClient(),
            audio_intake=_FakeIntake(tmp_path),
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
            aligner_client=StubAlignerClient(),
            audio_intake=_FakeIntake(tmp_path),
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
            aligner_client=StubAlignerClient(),
            audio_intake=_FakeIntake(tmp_path),
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
            aligner_client=StubAlignerClient(),
            audio_intake=_FakeIntake(tmp_path),
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
            aligner_client=StubAlignerClient(),
            audio_intake=_FakeIntake(tmp_path),
        )
    )

    resp = client.post(
        "/api/asr/transcribe",
        files={"file": ("a.wav", b"RIFF\x00\x00\x00\x00WAVE", "audio/wav")},
        data={"extra_terms": "[123]"},
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_EXTRA_TERMS"


class _RaisingAsr:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def health(self) -> bool:
        return True

    async def transcribe(self, audio, *, context):
        raise self._exc


@pytest.mark.parametrize(
    "exc, status",
    [(AsrTimeout(), 504), (AsrUnavailable(), 502)],
)
def test_transcribe_maps_upstream_asr_errors(tmp_path, exc, status):
    client = TestClient(
        create_app(
            settings=Settings(db_path=tmp_path / "t.db"),
            asr_client=_RaisingAsr(exc),
            aligner_client=StubAlignerClient(),
            audio_intake=_FakeIntake(tmp_path),
        )
    )

    resp = client.post(
        "/api/asr/transcribe",
        files={"file": ("a.wav", b"RIFF\x00\x00\x00\x00WAVE", "audio/wav")},
    )

    assert resp.status_code == status


def test_create_app_selects_client_by_use_stub_models(tmp_path):
    stub_app = create_app(settings=Settings(db_path=tmp_path / "s.db", use_stub_models=True))
    assert isinstance(stub_app.state.asr_client, StubAsrClient)
    assert isinstance(stub_app.state.aligner_client, StubAlignerClient)

    real_app = create_app(settings=Settings(db_path=tmp_path / "r.db", use_stub_models=False))
    assert isinstance(real_app.state.asr_client, VllmAsrClient)
    assert isinstance(real_app.state.aligner_client, HttpAlignerClient)


def test_transcribe_load_sheds_beyond_concurrency_limit(tmp_path):
    # 真 client 上線後併發辨識會搶 GPU；guard 達上限即 load-shed 503。
    from httpx import ASGITransport, AsyncClient

    release = asyncio.Event()
    result = TranscriptionResult(
        segments=[], raw_text="", transcription_only="", duration=0.0
    )

    class _BlockingAsr:
        async def health(self):
            return True

        async def transcribe(self, audio, *, context):
            await release.wait()
            return result

    app = create_app(
        settings=Settings(db_path=tmp_path / "t.db", max_concurrent_heavy_requests=1),
        asr_client=_BlockingAsr(),
        audio_intake=_FakeIntake(tmp_path),
    )
    files = {"file": ("a.wav", b"RIFF\x00\x00\x00\x00WAVE", "audio/wav")}

    async def scenario():
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as ac:
            first = asyncio.create_task(ac.post("/api/asr/transcribe", files=files))
            await asyncio.sleep(0.05)  # 讓 first 占住唯一 slot
            second = await ac.post("/api/asr/transcribe", files=files)
            release.set()
            return await first, second

    first_resp, second = asyncio.run(scenario())

    assert second.status_code == 503
    assert second.json()["error"]["code"] == "TOO_MANY_REQUESTS"
    assert first_resp.status_code == 200
