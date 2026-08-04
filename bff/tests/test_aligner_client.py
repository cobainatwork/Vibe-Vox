"""HttpAlignerClient：seam 為 httpx 傳輸層（MockTransport 注入假 aligner 回應）。

真的連 aligner 服務屬環境相依，不進測試；此處驗證逐段切片、multipart 組裝、
offset 拼接與錯誤映射的全部邏輯。aligner 的契約見 aligner/README.md。
"""

import asyncio
import email
import io
import json
import wave
from pathlib import Path

import httpx
import pytest

from vibe_vox.adapters.aligner import (
    AlignerTimeout,
    AlignerUnavailable,
    HttpAlignerClient,
)
from vibe_vox.adapters.base import Segment, Word

_RATE = 24000


def _wav(tmp_path, *, seconds: float = 3.0) -> Path:
    p = tmp_path / "a.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_RATE)
        w.writeframes(b"\x00\x00" * int(seconds * _RATE))
    return p


def _parse_multipart(request: httpx.Request) -> tuple[str, list[bytes]]:
    """解 multipart，回 (items 的 JSON 字串, 各 audio part 的 bytes)。"""
    msg = email.message_from_bytes(
        b"Content-Type: "
        + request.headers["content-type"].encode()
        + b"\r\n\r\n"
        + request.content
    )
    items = ""
    audio: list[bytes] = []
    for part in msg.get_payload():
        disposition = part.get("content-disposition", "")
        payload = part.get_payload(decode=True)
        if 'name="items"' in disposition:
            items = payload.decode()
        elif 'name="audio"' in disposition:
            audio.append(payload)
    return items, audio


def _nframes(wav: bytes) -> int:
    with wave.open(io.BytesIO(wav), "rb") as w:
        return w.getnframes()


def _client(handler, **kwargs) -> HttpAlignerClient:
    return HttpAlignerClient(
        "http://aligner:9100", transport=httpx.MockTransport(handler), **kwargs
    )


def _reply(items: list[dict]) -> httpx.Response:
    return httpx.Response(200, json={"items": items})


def test_align_sends_one_sliced_audio_per_segment(tmp_path):
    # 逐段切片並一次送完（batch）。切片左右各留 buffer 吸收 VibeVoice 切點漂移；
    # 第一段的 buffer 被音檔開頭夾掉，故其切片較短。
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return _reply([{"words": []}, {"words": []}])

    client = _client(handler, slice_buffer_seconds=0.5)
    segments = [
        Segment(Start=0.0, End=1.0, Speaker="0", Content="你好"),
        Segment(Start=1.0, End=2.0, Speaker="0", Content="再見"),
    ]
    asyncio.run(client.align(_wav(tmp_path), segments))

    req = captured["request"]
    assert req.method == "POST"
    assert req.url.path == "/align"
    items, audio = _parse_multipart(req)
    assert json.loads(items) == [{"text": "你好"}, {"text": "再見"}]
    assert len(audio) == 2
    assert _nframes(audio[0]) == int(1.5 * _RATE)  # [0.0, 1.5)：左 buffer 被夾掉
    assert _nframes(audio[1]) == int(2.0 * _RATE)  # [0.5, 2.5)


def test_align_shifts_timestamps_back_to_absolute_time(tmp_path):
    # aligner 回的時間基準是切片自身的 0，須加回該切片在原音檔的起點才是絕對時間。
    # 第一段的切片起點被夾限至 0（左 buffer 落在音檔外），故其時間戳不位移；
    # 第二段的切片自 0.5 起（Start 1.0 減 buffer 0.5），故整段加 0.5。
    def handler(request: httpx.Request) -> httpx.Response:
        return _reply(
            [
                {"words": [{"text": "你", "start": 0.12, "end": 0.30}]},
                {"words": [{"text": "再", "start": 0.60, "end": 0.85}]},
            ]
        )

    client = _client(handler, slice_buffer_seconds=0.5)
    segments = [
        Segment(Start=0.0, End=1.0, Speaker="0", Content="你好"),
        Segment(Start=1.0, End=2.0, Speaker="0", Content="再見"),
    ]
    result = asyncio.run(client.align(_wav(tmp_path), segments))

    assert result[0] == [Word(Text="你", Start=0.12, End=0.30)]
    assert result[1] == [Word(Text="再", Start=1.10, End=1.35)]


def test_align_skips_request_when_no_segments(tmp_path):
    # 音訊有效但完全無語音時 segments 為空（docs/api/asr.md §6）。aligner 的 audio
    # 為必填欄位，送零個檔會換來 400，故此情境不該發請求。
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("segments 為空時不應呼叫 aligner")

    result = asyncio.run(_client(handler).align(_wav(tmp_path), []))

    assert result == []


def test_align_rejects_item_count_mismatch(tmp_path):
    # 回傳筆數與送出段數不符時 zip 會靜默截短，使該段之後的 offset 全數錯位且
    # 無聲無息。視為上游違約。
    client = _client(lambda r: _reply([{"words": []}]))
    segments = [
        Segment(Start=0.0, End=1.0, Speaker="0", Content="你好"),
        Segment(Start=1.0, End=2.0, Speaker="0", Content="再見"),
    ]

    with pytest.raises(AlignerUnavailable):
        asyncio.run(client.align(_wav(tmp_path), segments))


def test_align_isolates_segments_the_service_would_reject(tmp_path):
    # Content 為空是既有行為（docs/api/asr.md §6：模型缺欄位補空字串）。整批送出
    # 會換來 400 INVALID_ITEMS 使整檔對齊失效，違反「單段對歪不污染其他段」
    # （ADR-0004）。故退化段落不送，其結果為空、其餘段照常。
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return _reply([{"words": [{"text": "再", "start": 0.60, "end": 0.85}]}])

    client = _client(handler, slice_buffer_seconds=0.5)
    segments = [
        Segment(Start=0.0, End=1.0, Speaker="0", Content=""),
        Segment(Start=1.0, End=2.0, Speaker="0", Content="再見"),
    ]
    result = asyncio.run(client.align(_wav(tmp_path), segments))

    items, audio = _parse_multipart(captured["request"])
    assert json.loads(items) == [{"text": "再見"}]  # 只送可對齊的那段
    assert len(audio) == 1
    assert result[0] == []  # 退化段落留空位，索引不位移
    assert result[1] == [Word(Text="再", Start=1.10, End=1.35)]


def test_align_isolates_segments_whose_slice_is_empty(tmp_path):
    # 時間戳幻覺使段落落在音檔外時切片為零長度，送出會讓推論失敗、整批回 500。
    def handler(request: httpx.Request) -> httpx.Response:
        return _reply([{"words": [{"text": "你", "start": 0.12, "end": 0.30}]}])

    client = _client(handler, slice_buffer_seconds=0.5)
    segments = [
        Segment(Start=0.0, End=1.0, Speaker="0", Content="你好"),
        Segment(Start=90.0, End=91.0, Speaker="0", Content="超出音檔"),
    ]
    result = asyncio.run(client.align(_wav(tmp_path, seconds=3.0), segments))

    assert result[0] == [Word(Text="你", Start=0.12, End=0.30)]
    assert result[1] == []


def test_align_skips_request_when_every_segment_degenerate(tmp_path):
    # 全部退化時沒有可送的內容，發請求只會換來 400。
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("無可對齊段落時不應呼叫 aligner")

    segments = [
        Segment(Start=0.0, End=1.0, Speaker="0", Content=""),
        Segment(Start=1.0, End=2.0, Speaker="0", Content="   "),
    ]
    result = asyncio.run(_client(handler).align(_wav(tmp_path), segments))

    assert result == [[], []]


def test_health_reports_ready_only_on_success_status():
    # aligner 未載入權重時回 503（見 aligner/README.md），故只認 200。
    assert asyncio.run(_client(lambda r: httpx.Response(200, json={"ready": True})).health())
    assert not asyncio.run(
        _client(lambda r: httpx.Response(503, json={"ready": False})).health()
    )


def test_health_reports_not_ready_when_unreachable():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    assert not asyncio.run(_client(handler).health())


def test_align_raises_timeout(tmp_path):
    def handler(request):
        raise httpx.TimeoutException("slow")

    segments = [Segment(Start=0.0, End=1.0, Speaker="0", Content="你好")]

    with pytest.raises(AlignerTimeout):
        asyncio.run(_client(handler).align(_wav(tmp_path), segments))


def test_align_raises_unavailable_on_connect_error(tmp_path):
    def handler(request):
        raise httpx.ConnectError("connection refused")

    segments = [Segment(Start=0.0, End=1.0, Speaker="0", Content="你好")]

    with pytest.raises(AlignerUnavailable):
        asyncio.run(_client(handler).align(_wav(tmp_path), segments))


def test_align_raises_unavailable_on_error_status(tmp_path):
    # 服務端的 4xx／5xx（BATCH_TOO_LARGE、ALIGN_FAILED、ALIGNER_NOT_READY 等）
    # 一律視為對齊不可得：字級時間戳是附加功能，逐字稿仍須照常回傳（ADR-0004）。
    client = _client(
        lambda r: httpx.Response(
            503, json={"error": {"code": "ALIGNER_NOT_READY", "message": "尚未就緒。"}}
        )
    )
    segments = [Segment(Start=0.0, End=1.0, Speaker="0", Content="你好")]

    with pytest.raises(AlignerUnavailable):
        asyncio.run(client.align(_wav(tmp_path), segments))


def test_align_raises_unavailable_on_non_json_body(tmp_path):
    # 回 200 但主體非 JSON（proxy 介入、服務被替換）時 resp.json() 拋
    # JSONDecodeError，它不屬 httpx.HTTPError。不攔就會冒成 500 使逐字稿一併失效，
    # 違反「aligner 全掛時逐字稿仍可取得」（ADR-0004 第二層降級）。
    client = _client(lambda r: httpx.Response(200, text="<html>502 Bad Gateway</html>"))
    segments = [Segment(Start=0.0, End=1.0, Speaker="0", Content="你好")]

    with pytest.raises(AlignerUnavailable):
        asyncio.run(client.align(_wav(tmp_path), segments))


@pytest.mark.parametrize(
    "body",
    [
        {},  # 缺 items
        {"items": None},  # items 非陣列
        {"items": [{}]},  # 筆內缺 words
        {"items": [{"words": [{"text": "你"}]}]},  # Word 缺時間戳
    ],
)
def test_align_raises_unavailable_on_malformed_envelope(tmp_path, body):
    # 回 200 但信封不合契約不得 crash 成 500（比照 VllmAsrClient 的信封防禦）。
    client = _client(lambda r: httpx.Response(200, json=body))
    segments = [Segment(Start=0.0, End=1.0, Speaker="0", Content="你好")]

    with pytest.raises(AlignerUnavailable):
        asyncio.run(client.align(_wav(tmp_path), segments))
