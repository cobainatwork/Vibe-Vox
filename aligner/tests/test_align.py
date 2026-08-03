"""對齊端點的成功路徑契約：多段音訊與文字進、字級時間戳出。"""

import json

from fastapi.testclient import TestClient

from vibe_vox_aligner.main import create_app

from fakes import FakeAligner, wav_bytes


def _audio(*names: str, seconds: float = 1.0, sample_rate: int = 24000) -> list:
    return [
        ("audio", (name, wav_bytes(seconds, sample_rate), "audio/wav")) for name in names
    ]


def test_align_single_returns_word_timestamps() -> None:
    client = TestClient(create_app(aligner=FakeAligner()))

    resp = client.post(
        "/align",
        data={"items": json.dumps([{"text": "你好"}])},
        files=_audio("seg0.wav"),
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "items": [
            {
                "words": [
                    {"text": "你", "start": 0.0, "end": 0.5},
                    {"text": "好", "start": 1.0, "end": 1.5},
                ]
            }
        ]
    }


def test_align_batch_preserves_request_order() -> None:
    """T2 以段落索引拼回 offset，故回應順序必須與送出的順序一致。"""
    client = TestClient(create_app(aligner=FakeAligner()))

    resp = client.post(
        "/align",
        data={"items": json.dumps([{"text": "甲"}, {"text": "乙"}, {"text": "丙"}])},
        files=_audio("seg0.wav", "seg1.wav", "seg2.wav"),
    )

    assert resp.status_code == 200
    assert [item["words"][0]["text"] for item in resp.json()["items"]] == ["甲", "乙", "丙"]


def test_align_always_requests_chinese() -> None:
    """語言不開放呼叫端指定：送進來的一律是 ASR 的中文逐字稿。"""
    fake = FakeAligner()
    client = TestClient(create_app(aligner=fake))

    client.post(
        "/align",
        data={"items": json.dumps([{"text": "甲"}, {"text": "乙"}])},
        files=_audio("seg0.wav", "seg1.wav"),
    )

    _, _, languages = fake.calls[0]
    assert languages == ["Chinese", "Chinese"]


def test_align_accepts_segment_sized_audio() -> None:
    """實際段長 30–40 秒的 24 kHz wav 逾 1.4 MB，不得被 multipart 的 per-part 上限擋下。"""
    client = TestClient(create_app(aligner=FakeAligner()))

    resp = client.post(
        "/align",
        data={"items": json.dumps([{"text": "甲"}])},
        files=_audio("seg0.wav", seconds=30.0),
    )

    assert resp.status_code == 200


def test_align_passes_decoded_waveform_and_sample_rate() -> None:
    """音訊須以 (ndarray, sr) 交給模型——免落地暫存檔，且取樣率不被竄改。"""
    fake = FakeAligner()
    client = TestClient(create_app(aligner=fake))

    client.post(
        "/align",
        data={"items": json.dumps([{"text": "甲"}])},
        files=_audio("seg0.wav", seconds=2.0, sample_rate=24000),
    )

    waveforms, texts, _ = fake.calls[0]
    waveform, sample_rate = waveforms[0]
    assert sample_rate == 24000
    assert waveform.shape == (48000,)
    assert texts == ["甲"]
