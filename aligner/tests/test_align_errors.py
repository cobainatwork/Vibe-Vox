"""對齊端點的錯誤契約。信封形狀與 BFF 一致：{"error": {"code", "message"}}。"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from vibe_vox_aligner.aligner import Word
from vibe_vox_aligner.config import Settings
from vibe_vox_aligner.main import create_app

from fakes import FakeAligner, wav_bytes


def _client(**settings_overrides) -> TestClient:
    return TestClient(
        create_app(aligner=FakeAligner(), settings=Settings(**settings_overrides))
    )


def _wav_file(seconds: float = 1.0, name: str = "seg0.wav") -> tuple:
    return ("audio", (name, wav_bytes(seconds), "audio/wav"))


def test_rejects_malformed_items_json() -> None:
    resp = _client().post("/align", data={"items": "not json"}, files=[_wav_file()])

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_ITEMS"


def test_rejects_items_that_are_not_a_list() -> None:
    resp = _client().post(
        "/align", data={"items": json.dumps({"text": "甲"})}, files=[_wav_file()]
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_ITEMS"


def test_rejects_blank_text() -> None:
    """空文字沒有可對齊的單位，擋在此處以免白跑一次 GPU 推論。"""
    resp = _client().post(
        "/align", data={"items": json.dumps([{"text": "  "}])}, files=[_wav_file()]
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_ITEMS"


def test_rejects_batch_size_mismatch() -> None:
    """數量不符時無法確定哪段配哪句，靜默截短會讓 T2 的 offset 全錯位。"""
    resp = _client().post(
        "/align",
        data={"items": json.dumps([{"text": "甲"}, {"text": "乙"}])},
        files=[_wav_file()],
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "BATCH_SIZE_MISMATCH"


def test_rejects_batch_over_item_limit() -> None:
    """batch 過大撞的是 VRAM，失敗會呈現為 CUDA OOM 並波及共用同卡的 vllm 與 tts；
    擋在請求層才有明確錯誤可回。"""
    resp = _client(max_batch_items=2).post(
        "/align",
        data={"items": json.dumps([{"text": "甲"}, {"text": "乙"}, {"text": "丙"}])},
        files=[_wav_file(name=f"seg{i}.wav") for i in range(3)],
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "BATCH_TOO_LARGE"


def test_batch_limit_is_checked_before_reading_files() -> None:
    """段數上限存在的目的就是別把大請求收進記憶體，故須在讀檔前擋。

    段數與數量不符兩個問題同時存在時回 BATCH_TOO_LARGE 而非 BATCH_SIZE_MISMATCH，
    即證明前者的判斷發生在讀檔（與配對）之前。
    """
    resp = _client(max_batch_items=2).post(
        "/align",
        data={"items": json.dumps([{"text": "甲"}, {"text": "乙"}])},
        files=[_wav_file(name=f"seg{i}.wav") for i in range(5)],
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "BATCH_TOO_LARGE"


def test_rejects_undecodable_audio() -> None:
    resp = _client().post(
        "/align",
        data={"items": json.dumps([{"text": "甲"}])},
        files=[("audio", ("seg0.wav", b"definitely not audio", "audio/wav"))],
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "AUDIO_DECODE_ERROR"


def test_rejects_audio_over_duration_limit() -> None:
    """逾 qwen-asr 的對齊輸入上限時模型會靜默對歪，故拒收而非硬送。"""
    resp = _client(max_audio_seconds=1.0).post(
        "/align",
        data={"items": json.dumps([{"text": "甲"}])},
        files=[_wav_file(seconds=2.0)],
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "AUDIO_TOO_LONG"


def test_missing_fields_return_validation_error_envelope() -> None:
    resp = _client().post("/align", data={"items": json.dumps([{"text": "甲"}])})

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_rejects_when_model_not_loaded() -> None:
    """權重尚未就緒時明確回 503，而非讓請求撞上 None 拿到 500。"""
    client = TestClient(create_app(aligner=None))

    resp = client.post(
        "/align", data={"items": json.dumps([{"text": "甲"}])}, files=[_wav_file()]
    )

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "ALIGNER_NOT_READY"


def test_reports_inference_failure() -> None:
    client = TestClient(create_app(aligner=FakeAligner(error=RuntimeError("CUDA OOM"))))

    resp = client.post(
        "/align", data={"items": json.dumps([{"text": "甲"}])}, files=[_wav_file()]
    )

    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "ALIGN_FAILED"


def test_load_sheds_beyond_concurrency_limit() -> None:
    """達上限直接回 503 不排隊：GPU 非本服務獨佔，堆疊請求會撞 OOM（ADR-0004）。"""
    entered = threading.Event()
    release = threading.Event()

    class BlockingAligner:
        def align(self, waveforms, texts, languages):
            entered.set()
            release.wait(timeout=10)
            return [[Word(text="甲", start=0.0, end=0.5)] for _ in texts]

    app = create_app(aligner=BlockingAligner(), settings=Settings(max_concurrent_requests=1))
    payload = {"items": json.dumps([{"text": "甲"}])}

    def post(client: TestClient):
        return client.post("/align", data=payload, files=[_wav_file()])

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(post, TestClient(app))
        assert entered.wait(timeout=10), "第一個請求未進入對齊"
        second = post(TestClient(app))
        release.set()
        assert first.result(timeout=10).status_code == 200

    assert second.status_code == 503
    assert second.json()["error"]["code"] == "TOO_MANY_REQUESTS"
