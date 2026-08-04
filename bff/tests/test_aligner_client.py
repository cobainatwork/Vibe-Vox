"""HttpAlignerClient：seam 為 httpx 傳輸層（MockTransport 注入假 aligner 回應）。

真的連 aligner 服務屬環境相依，不進測試；此處驗證逐段切片、multipart 組裝、
offset 拼接與錯誤映射的全部邏輯。aligner 的契約見 aligner/README.md。
"""

import asyncio
import email
import io
import json
import logging
import wave
from pathlib import Path

import httpx
import pytest

from vibe_vox.adapters.aligner import (
    DEFAULT_MAX_BATCH_ITEMS,
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


def _numbered_segments(count: int) -> list[Segment]:
    """count 個各 1 秒的連續段落，Content 為 `第N段`（N 自 0 起）。"""
    return [
        Segment(Start=float(i), End=float(i + 1), Speaker="0", Content=f"第{i}段")
        for i in range(count)
    ]


def _second_batch_fails(request: httpx.Request) -> httpx.Response:
    """配 `max_batch_items=2` 與四個段落：第二批回 500，第一批照常回結果。

    以 Content 而非請求次序判斷，故不依賴 handler 被呼叫的順序。
    """
    items, audio = _parse_multipart(request)
    if json.loads(items)[0]["text"] == "第2段":
        return httpx.Response(
            500, json={"error": {"code": "ALIGN_FAILED", "message": "推論失敗。"}}
        )
    return _reply(
        [{"words": [{"text": "字", "start": 0.1, "end": 0.3}]} for _ in audio]
    )


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


def test_align_splits_into_batches_within_the_service_limit(tmp_path):
    # 服務端對單次請求有段數上限（VRAM 保護，aligner 的 max_batch_items）。超過即整批
    # 回 400 BATCH_TOO_LARGE，全段拿不到時間戳，而那正是 #36 的實際故障：10 分鐘會議
    # 錄音切出 63 段。上限由 client 端的設定知道、先於送出就遵守，不靠撞牆後重試。
    sent: list[list[dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        items, audio = _parse_multipart(request)
        sent.append(json.loads(items))
        return _reply([{"words": []} for _ in audio])

    client = _client(handler, slice_buffer_seconds=0.5, max_batch_items=2)

    result = asyncio.run(
        client.align(_wav(tmp_path, seconds=10.0), _numbered_segments(5))
    )

    assert [len(batch) for batch in sent] == [2, 2, 1]
    # 順序不得被分批打亂：offset 拼接依賴 items 與 audio 的順序一一對應。
    assert [item["text"] for batch in sent for item in batch] == [
        f"第{i}段" for i in range(5)
    ]
    assert len(result) == 5


def test_align_splits_the_meeting_recording_at_the_default_limit(tmp_path):
    # #36 的驗收情境本身，用**預設**上限而非測試用的小值：10 分鐘會議錄音切出 63 段，
    # 扣掉 6 個非語音標記段（#38）後送出 57 段。
    #
    # 上限為何是 8 而非當初校準的 32：32 在真機上 CUDA OOM，兩批都失敗。計數式上限對
    # 記憶體的主導變數是盲的——批次張量 pad 到該批最長的段落，而校準用的是同一段 34 秒
    # 音訊重複 32 次，padding 浪費恰好 1.00 倍。真實錄音的段長是 1.77 至 41.29 秒，
    # cap 32 之下 621 秒的實際音訊會被 pad 成 1206 秒。詳見 DEFAULT_MAX_BATCH_ITEMS。
    sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        _, audio = _parse_multipart(request)
        sizes.append(len(audio))
        return _reply([{"words": []} for _ in audio])

    client = _client(handler, slice_buffer_seconds=0.5)
    # 六個非語音標記段散在其中，故此處同時涵蓋「剔除與分批的交互」：剔除發生在分批之前，
    # 批次大小算的是可送出的段數而非原段數。
    segments = _numbered_segments(63)
    for index, marker in ((0, "[Silence]"), (13, "[Unintelligible Speech]"),
                          (15, "[Unintelligible Speech]"), (17, "[Silence]"),
                          (20, "[Silence]"), (22, "[Music]")):
        segments[index] = segments[index].model_copy(update={"Content": marker})

    result = asyncio.run(client.align(_wav(tmp_path, seconds=70.0), segments))

    assert sizes == [8, 8, 8, 8, 8, 8, 8, 1]  # 63 − 6 = 57 段送出
    assert max(sizes) <= DEFAULT_MAX_BATCH_ITEMS
    assert len(result) == 63  # 回傳仍與原段數對齊，標記段留空位
    assert result[0] == [] and result[13] == []


def test_align_isolates_a_failed_batch_from_the_others(tmp_path):
    # 分批後故障隔離的層級從段落升到批次：某批回 400 或 500 時，其他批已取得的時間戳
    # 不該被丟棄（#36 驗收明列）。與 #27 的段落級隔離同一原則，只是層級不同。
    client = _client(_second_batch_fails, slice_buffer_seconds=0.5, max_batch_items=2)

    result = asyncio.run(
        client.align(_wav(tmp_path, seconds=10.0), _numbered_segments(4))
    )

    # 批次為 `第0段`／`第1段` 與 `第2段`／`第3段`，後者失敗。
    assert result[0] == [Word(Text="字", Start=0.1, End=0.3)]
    assert result[1] != []
    assert result[2] == []
    assert result[3] == []


def test_align_logs_the_reason_a_batch_was_dropped(tmp_path, caplog):
    # 分批帶來一條新的靜默降級路徑：一批失敗、其他批成功，故不 raise，端點層那條服務
    # 層級的 log 不會出現。那些段落仍悄悄變成未對齊，而 merge_alignment 只會說「字級
    # 清單為空」，看不出有一批整批失敗（#37）。
    client = _client(_second_batch_fails, slice_buffer_seconds=0.5, max_batch_items=2)

    with caplog.at_level(logging.WARNING, logger="vibe_vox.adapters.aligner"):
        asyncio.run(client.align(_wav(tmp_path, seconds=10.0), _numbered_segments(4)))

    assert "ALIGN_FAILED" in caplog.text
    assert "推論失敗" in caplog.text


def test_align_logs_the_other_causes_when_batches_fail_differently(tmp_path, caplog):
    # 跨批不保證同因：一批逾時、另一批回 503 是可能的（服務在兩次請求之間被重啟）。
    # 只有一個例外能往上傳，其餘批次的原因若不記下就完全消失，而 #37 的驗收正是
    # 「log 能直接指出原因而不需反推」。
    def handler(request: httpx.Request) -> httpx.Response:
        items, _ = _parse_multipart(request)
        if json.loads(items)[0]["text"] == "第0段":
            raise httpx.TimeoutException("slow")
        return httpx.Response(
            503, json={"error": {"code": "ALIGNER_NOT_READY", "message": "尚未就緒。"}}
        )

    client = _client(handler, slice_buffer_seconds=0.5, max_batch_items=2)

    with caplog.at_level(logging.WARNING, logger="vibe_vox.adapters.aligner"):
        with pytest.raises(AlignerTimeout):
            asyncio.run(
                client.align(_wav(tmp_path, seconds=10.0), _numbered_segments(4))
            )

    # 第一批的逾時往上傳、由端點層記錄；第二批的原因必須在此留下。
    assert "ALIGNER_NOT_READY" in caplog.text


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


def test_align_skips_non_speech_marker_segments(tmp_path):
    # VibeVoice 對非語音區段輸出方括號標記（[Silence]／[Music]／[Unintelligible
    # Speech]）。它們的 Content 非空，故會被送去對齊，而 qwen-asr 的 clean_token 剝掉
    # 方括號後「Silence」成為一個 Word，模型會把它對到那段靜音上，產出假的字級時間戳。
    #
    # 危害具體：本次資料的第一段正是 [Silence]（0 至 2.45 秒），該假 Word 若通過判準，
    # speech_start 會變成約 0，等於宣稱沒有開頭沉默，而那 2.45 秒本身就是開頭沉默。
    # ADR-0004 明文要求開頭沉默須完整保留（#38）。
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return _reply([{"words": [{"text": "你", "start": 0.12, "end": 0.30}]}])

    client = _client(handler, slice_buffer_seconds=0.5)
    segments = [
        Segment(Start=0.0, End=2.45, Speaker="", Content="[Silence]"),
        Segment(Start=2.45, End=3.45, Speaker="0", Content="你好"),
    ]

    result = asyncio.run(client.align(_wav(tmp_path, seconds=5.0), segments))

    items, audio = _parse_multipart(captured["request"])
    assert json.loads(items) == [{"text": "你好"}]
    assert len(audio) == 1
    assert result[0] == []  # 標記段留空位，走 empty_words 路徑，語義正確
    assert result[1] == [Word(Text="你", Start=2.07, End=2.25)]


def test_align_still_sends_segments_that_merely_contain_brackets(tmp_path):
    # 防過度過濾：只有「整段就是一個標記」才是非語音。含方括號但另有實際文字的段落仍
    # 是語音，剔除它等於白白丟棄可用的時間戳，而那是判準放寬的方向錯誤（比照 #34）。
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return _reply([{"words": [{"text": "你", "start": 0.12, "end": 0.30}]}])

    client = _client(handler, slice_buffer_seconds=0.5)
    segments = [Segment(Start=0.0, End=1.0, Speaker="0", Content="你好[Music]")]

    result = asyncio.run(client.align(_wav(tmp_path), segments))

    items, _ = _parse_multipart(captured["request"])
    assert json.loads(items) == [{"text": "你好[Music]"}]
    assert result[0] == [Word(Text="你", Start=0.12, End=0.30)]


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


def test_unavailable_carries_the_service_error_code(tmp_path):
    # aligner 的回應體已寫明原因（README 定義九個錯誤碼），丟棄它會讓 log 只剩
    # `AlignerUnavailable()` 空括號：看不出是 400 還是 503、更看不出是哪個碼。#36 的
    # 診斷因此得從「63 段」反推，而現成的答案本來就在回應體裡（#37）。
    client = _client(
        lambda r: httpx.Response(
            400,
            json={
                "error": {
                    "code": "BATCH_TOO_LARGE",
                    "message": "單次 63 段超過上限 32 段，請分批送。",
                }
            },
        )
    )
    segments = [Segment(Start=0.0, End=1.0, Speaker="0", Content="你好")]

    with pytest.raises(AlignerUnavailable) as raised:
        asyncio.run(client.align(_wav(tmp_path), segments))

    described = str(raised.value)
    assert "400" in described
    assert "BATCH_TOO_LARGE" in described
    assert "請分批送" in described


def test_align_raises_unavailable_on_non_json_body(tmp_path):
    # 回 200 但主體非 JSON（proxy 介入、服務被替換）時 resp.json() 拋
    # JSONDecodeError，它不屬 httpx.HTTPError。不攔就會冒成 500 使逐字稿一併失效，
    # 違反「aligner 全掛時逐字稿仍可取得」（ADR-0004 第二層降級）。
    client = _client(lambda r: httpx.Response(200, text="<html>502 Bad Gateway</html>"))
    segments = [Segment(Start=0.0, End=1.0, Speaker="0", Content="你好")]

    with pytest.raises(AlignerUnavailable):
        asyncio.run(client.align(_wav(tmp_path), segments))


def test_unavailable_describes_a_non_json_success_body(tmp_path):
    # 200 但主體是 HTML（反向代理介入、服務被換成別的東西）。這是最容易被誤讀成「對齊
    # 品質不佳」的故障，因為它不表現為任何錯誤狀態碼，故 log 必須指出主體不是 JSON。
    client = _client(lambda r: httpx.Response(200, text="<html>502 Bad Gateway</html>"))
    segments = [Segment(Start=0.0, End=1.0, Speaker="0", Content="你好")]

    with pytest.raises(AlignerUnavailable) as raised:
        asyncio.run(client.align(_wav(tmp_path), segments))

    described = str(raised.value)
    assert "非 JSON" in described
    assert "Bad Gateway" in described


def test_unavailable_describes_an_item_count_mismatch(tmp_path):
    # 「服務回的筆數不對」與「服務掛了」是不同的故障，log 若都印成空括號就會把人導向
    # 錯誤的方向：前者要查服務端的實作，後者要查部署（#37）。
    client = _client(lambda r: _reply([{"words": []}, {"words": []}]))
    segments = [Segment(Start=0.0, End=1.0, Speaker="0", Content="你好")]

    with pytest.raises(AlignerUnavailable) as raised:
        asyncio.run(client.align(_wav(tmp_path), segments))

    described = str(raised.value)
    assert "送出 1" in described
    assert "回 2" in described


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
