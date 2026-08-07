"""真模型串接 VllmOmniTtsClient：seam 為 httpx 傳輸層（MockTransport 注入假回應）。

真的連遠端 vLLM-Omni 屬環境相依，不進測試；此處驗證組請求、音訊後處理、遠端錯誤
映射的全部邏輯。端點的實際欄位行為以逐行讀原始碼取證，見
docs/superpowers/specs/2026-08-05-voxcpm2-serving-transport.md。
"""

import asyncio
import io
import json
import shutil
import wave
from pathlib import Path

import httpx
import pytest

from vibe_vox.adapters.base import (
    CONTRACT_SPEC,
    TtsTimeout,
    TtsUnavailable,
    Utterance,
)
from vibe_vox.adapters.vllm_omni_tts import VllmOmniTtsClient
from vibe_vox.audio.wav import PcmAudio

need_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")


def _wav_bytes(seconds: float = 1.0, rate: int = 24000, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate) * channels)
    return buf.getvalue()


def _ref(tmp_path) -> Path:
    p = tmp_path / "ref.wav"
    p.write_bytes(_wav_bytes())
    return p


def _captured_payload(tmp_path, utterances: list[Utterance]) -> dict:
    """跑一次 synthesize，取出送給端點的 JSON payload。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, content=_wav_bytes())

    client = VllmOmniTtsClient(
        "http://tts:8000", "voxcpm2", transport=httpx.MockTransport(handler)
    )
    asyncio.run(client.synthesize(utterances, reference_audio=_ref(tmp_path)))
    return captured


def test_unreadable_reference_audio_becomes_a_declared_error_mode(tmp_path):
    """參考音讀不到時翻成 interface 宣告的錯誤模式，不讓 OSError 逸出。

    可讀是呼叫端的前置條件（建立時的不變量加端點的檢查，見 audio/reference.py），故這裡
    處理的是那兩道之後的殘餘：端點檢查與 adapter 讀檔之間的時間差。翻譯漏掉的例外會穿過
    HeavyRequestGuard 冒成 500，而 500 不在 docs/api/tts.md §6 的錯誤表內。
    """
    client = VllmOmniTtsClient(
        "http://tts:8000",
        "voxcpm2",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=_wav_bytes())),
    )

    with pytest.raises(TtsUnavailable):
        asyncio.run(
            client.synthesize(
                [Utterance(text="您好")], reference_audio=tmp_path / "gone.wav"
            )
        )


def test_request_carries_reference_audio_without_ref_text(tmp_path):
    # ref_audio 走 data: base64（不用 file://，那需要 server 開 --allowed-local-media-path
    # 而擴大檔案系統暴露面）。**不得送 ref_text**：送了會落到 continuation 模式並讓
    # 行內風格失效（傳輸 findings §3.2.1）。
    payload = _captured_payload(tmp_path, [Utterance(text="您好")])

    assert payload["ref_audio"].startswith("data:audio/wav;base64,")
    assert "ref_text" not in payload


def test_reference_audio_mime_follows_the_actual_container(tmp_path):
    # 參考音原樣落地、檔名是 uuid 無副檔名（api/admin_voices.py 不轉碼），故容器只能從
    # 檔頭嗅。寫死 audio/wav 會讓 mp3 音色的 data URI 謊報容器。
    mp3 = tmp_path / "ref.mp3"
    mp3.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00fake")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, content=_wav_bytes())

    client = VllmOmniTtsClient(
        "http://tts:8000", "voxcpm2", transport=httpx.MockTransport(handler)
    )
    asyncio.run(client.synthesize([Utterance(text="您好")], reference_audio=mp3))

    assert captured["ref_audio"].startswith("data:audio/mpeg;base64,")


def test_instruct_becomes_inline_prefix_of_input(tmp_path):
    # 風格的唯一通道是 input 的行內 (...) 前綴，語法對齊官方 CLI 的 build_final_text。
    payload = _captured_payload(
        tmp_path, [Utterance(text="您好", instruct="語速偏快、音量略大")]
    )

    assert payload["input"] == "(語速偏快、音量略大)您好"


def test_request_omits_silently_ignored_style_fields(tmp_path):
    # instructions 與 task_type 對 VoxCPM2 從未被讀取且不報錯（findings §3.2.2）：
    # 送了會回 200 加一段沒套用該風格的音訊。不送，以免後人以為風格走那條路。
    payload = _captured_payload(
        tmp_path, [Utterance(text="您好", instruct="語速偏快")]
    )

    assert "instructions" not in payload
    assert "task_type" not in payload


def _synthesized(tmp_path, utterances: list[Utterance], reply: bytes) -> PcmAudio:
    """跑一次 synthesize，回帶規格的 PCM。"""
    client = VllmOmniTtsClient(
        "http://tts:8000",
        "voxcpm2",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=reply)),
    )
    return asyncio.run(client.synthesize(utterances, reference_audio=_ref(tmp_path)))


def _frame_count(audio: PcmAudio) -> int:
    return len(audio.frames) // (audio.spec.channels * audio.spec.sample_width)


def test_multiple_utterances_are_concatenated(tmp_path):
    # 一次呼叫只承載一種風格，故逐句各一次請求；回給呼叫端的是串接後的單一段音訊。
    out = _synthesized(
        tmp_path,
        [Utterance(text="第一句"), Utterance(text="第二句"), Utterance(text="第三句")],
        _wav_bytes(seconds=1.0),
    )

    assert _frame_count(out) == 3 * 24000


def test_output_already_at_contract_spec_is_not_re_encoded(tmp_path):
    # 端點目前回 48 kHz，但規格若哪天對上了就不該再繞一次 ffmpeg——重編碼是損失，
    # 子進程往返是延遲。相同規格直接放行。
    out = _synthesized(tmp_path, [Utterance(text="您好")], _wav_bytes(rate=24000))

    assert out.spec == CONTRACT_SPEC


@need_ffmpeg
def test_forty_eight_k_response_is_downsampled_to_contract_spec(tmp_path):
    # 端點實際回 48 kHz mono（findings §3.3.4），消費端契約要 24 kHz／mono／16-bit。
    out = _synthesized(
        tmp_path, [Utterance(text="您好")], _wav_bytes(seconds=1.0, rate=48000)
    )

    assert out.spec == CONTRACT_SPEC
    assert _frame_count(out) == 24000


def _synthesize_with(tmp_path, handler) -> bytes:
    client = VllmOmniTtsClient(
        "http://tts:8000", "voxcpm2", transport=httpx.MockTransport(handler)
    )
    return asyncio.run(
        client.synthesize([Utterance(text="您好")], reference_audio=_ref(tmp_path))
    )


def test_upstream_timeout_maps_to_tts_timeout(tmp_path):
    def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(TtsTimeout):
        _synthesize_with(tmp_path, handler)


def test_upstream_error_status_maps_to_tts_unavailable(tmp_path):
    with pytest.raises(TtsUnavailable):
        _synthesize_with(tmp_path, lambda _: httpx.Response(500, text="boom"))


def test_unparseable_audio_maps_to_tts_unavailable(tmp_path):
    # 端點對超界參考音等狀況會回 ValueError 的文字而非我方的錯誤碼；那不是音訊，
    # 當成上游不可用而非讓 wave 的解析例外冒成 500。
    with pytest.raises(TtsUnavailable):
        _synthesize_with(tmp_path, lambda _: httpx.Response(200, text="not audio"))
