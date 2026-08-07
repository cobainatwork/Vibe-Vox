"""真模型串接 VllmAsrClient：seam 為 httpx 傳輸層（MockTransport 注入假 vLLM 回應）。

真的連遠端 vLLM 屬環境相依，不進測試；此處驗證組請求、解析回應、遠端錯誤映射的全部邏輯。
"""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from vibe_vox.adapters.base import AsrTimeout, AsrUnavailable
from vibe_vox.adapters.vllm_asr import VllmAsrClient


def _wav(tmp_path) -> Path:
    p = tmp_path / "a.wav"
    p.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
    return p


def _reply(content: str) -> httpx.Response:
    return httpx.Response(
        200, json={"choices": [{"message": {"content": content}}]}
    )


def _captured_prompt(tmp_path, *, context: str) -> str:
    """跑一次 transcribe，取出送給模型的 prompt 文字。"""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        parts = json.loads(request.content)["messages"][-1]["content"]
        captured["text"] = next(p for p in parts if p.get("type") == "text")["text"]
        return _reply("{}")

    client = VllmAsrClient(
        "http://vllm:8000", "m", transport=httpx.MockTransport(handler)
    )
    asyncio.run(client.transcribe(_wav(tmp_path), context=context))
    return captured["text"]


def test_prompt_field_labels_match_training_format(tmp_path):
    # 欄位描述須用 processor 訓練時的 show_keys（Start time/End time/Speaker ID），
    # 非 gradio demo 誤植的輸出 key 名。模型只在訓練中看過前者。
    text = _captured_prompt(tmp_path, context="")

    assert "these keys: Start time, End time, Speaker ID, Content" in text


def test_prompt_embeds_hotwords_with_training_phrasing(tmp_path):
    # hotword 須以訓練措辭 "with extra info:" 接在秒數之後，而非另起
    # "Context information" 區塊——後者不在模型的訓練分布內。
    text = _captured_prompt(tmp_path, context="台積電")

    assert text.startswith(
        "This is a 1.00 seconds audio, with extra info: 台積電\n\n"
        "Please transcribe it with these keys: Start time, End time, Speaker ID, Content"
    )
    assert "Context information" not in text


def test_transcribe_builds_request_and_parses_segments(tmp_path):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return _reply(
            json.dumps(
                {"segments": [{"Start": 0.0, "End": 1.2, "Speaker": "A", "Content": "你好"}]}
            )
        )

    client = VllmAsrClient(
        "http://vllm:8000", "vibevoice-asr", transport=httpx.MockTransport(handler)
    )
    result = asyncio.run(client.transcribe(_wav(tmp_path), context="台積電"))

    req = captured["request"]
    assert req.method == "POST"
    assert req.url.path == "/v1/chat/completions"
    body = json.loads(req.content)
    assert body["model"] == "vibevoice-asr"
    assert body["temperature"] == 0  # greedy 解碼
    assert body["top_p"] == 1.0
    assert body["repetition_penalty"] == 1.1  # 抑制 repetition/hallucination 迴圈
    assert body["max_tokens"] > 0  # 動態上限，防無上限 hallucination
    assert body["messages"][0]["role"] == "system"  # 官方契約：system 定調 JSON 轉錄
    parts = body["messages"][-1]["content"]
    audio = next(p for p in parts if p.get("type") == "audio_url")
    assert audio["audio_url"]["url"].startswith("data:audio/wav;base64,")  # data URL
    text = next(p for p in parts if p.get("type") == "text")["text"]
    assert "Start time, End time, Speaker ID, Content" in text  # 訓練格式的欄位描述
    assert "台積電" in text  # Hotword context 併入
    assert "繁體中文" in text  # 強制輸出繁體

    assert len(result.segments) == 1
    assert result.segments[0].Speaker == "A"
    assert result.segments[0].Content == "你好"
    assert result.transcription_only == "你好"


def test_parse_accepts_training_key_variants(tmp_path):
    # 模型有時直接拿 prompt 的欄位描述當 JSON key（官方 gradio demo 亦做三重相容）。
    # 無 fallback 時 Start/End 會靜默變 0.0——時間戳全毀卻不報錯。
    payload = json.dumps(
        {
            "segments": [
                {"Start time": 1.5, "End time": 3.0, "Speaker ID": 2, "Content": "測試"}
            ]
        },
        ensure_ascii=False,
    )
    client = VllmAsrClient(
        "http://vllm:8000", "m", transport=httpx.MockTransport(lambda r: _reply(payload))
    )
    result = asyncio.run(client.transcribe(_wav(tmp_path), context=""))

    assert result.segments[0].Start == 1.5
    assert result.segments[0].End == 3.0
    assert result.segments[0].Speaker == "2"


def test_transcribe_converts_simplified_content_to_traditional(tmp_path):
    # 模型輸出簡體字形；segments/transcription_only 轉台灣繁體（s2tw 純字形，
    # 不改詞彙），raw_text 保留模型原始輸出供 debug。
    simplified = json.dumps(
        {"segments": [{"Start": 0.0, "End": 2.0, "Speaker": "A", "Content": "以真实口吻传达情感"}]},
        ensure_ascii=False,
    )
    client = VllmAsrClient(
        "http://vllm:8000", "m", transport=httpx.MockTransport(lambda r: _reply(simplified))
    )
    result = asyncio.run(client.transcribe(_wav(tmp_path), context=""))

    assert result.segments[0].Content == "以真實口吻傳達情感"
    assert result.transcription_only == "以真實口吻傳達情感"
    assert "真实" in result.raw_text  # 原始輸出保留簡體


def test_transcribe_converts_plain_text_fallback_to_traditional(tmp_path):
    # 非 JSON 純文字退路也要轉繁；raw_text 仍保留原始。
    client = VllmAsrClient(
        "http://vllm:8000",
        "m",
        transport=httpx.MockTransport(lambda r: _reply("传达情感")),
    )
    result = asyncio.run(client.transcribe(_wav(tmp_path), context=""))

    assert result.transcription_only == "傳達情感"
    assert result.raw_text == "传达情感"


def test_transcribe_defends_against_non_json_content(tmp_path):
    # 模型回非預期輸出（非 JSON）不得崩潰；退回純文字。
    client = VllmAsrClient(
        "http://vllm:8000",
        "m",
        transport=httpx.MockTransport(lambda r: _reply("純文字，不是 JSON")),
    )
    result = asyncio.run(client.transcribe(_wav(tmp_path), context=""))

    assert result.segments == []
    assert result.raw_text == "純文字，不是 JSON"
    assert result.transcription_only == "純文字，不是 JSON"


def test_transcribe_defends_against_missing_fields(tmp_path):
    # segment 缺欄位不得崩潰；缺的補預設。
    client = VllmAsrClient(
        "http://vllm:8000",
        "m",
        transport=httpx.MockTransport(
            lambda r: _reply(json.dumps({"segments": [{"Content": "缺時間戳"}]}))
        ),
    )
    result = asyncio.run(client.transcribe(_wav(tmp_path), context=""))

    assert len(result.segments) == 1
    assert result.segments[0].Content == "缺時間戳"
    assert result.segments[0].Start == 0.0


def test_transcribe_raises_asr_unavailable_on_connect_error(tmp_path):
    def handler(r):
        raise httpx.ConnectError("connection refused")

    client = VllmAsrClient(
        "http://vllm:8000", "m", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(AsrUnavailable):
        asyncio.run(client.transcribe(_wav(tmp_path), context=""))


def test_transcribe_raises_asr_timeout(tmp_path):
    def handler(r):
        raise httpx.TimeoutException("slow")

    client = VllmAsrClient(
        "http://vllm:8000", "m", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(AsrTimeout):
        asyncio.run(client.transcribe(_wav(tmp_path), context=""))


def test_transcribe_raises_on_malformed_envelope(tmp_path):
    # vLLM 回 200 但信封異常（choices 空）不得 crash 成 500；視為上游不可用。
    client = VllmAsrClient(
        "http://vllm:8000",
        "m",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"choices": []})
        ),
    )
    with pytest.raises(AsrUnavailable):
        asyncio.run(client.transcribe(_wav(tmp_path), context=""))


def test_transcribe_raises_on_null_content(tmp_path):
    # message.content 為 null（OpenAI schema 合法）不得拋 TypeError 成 500。
    client = VllmAsrClient(
        "http://vllm:8000",
        "m",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, json={"choices": [{"message": {"content": None}}]}
            )
        ),
    )
    with pytest.raises(AsrUnavailable):
        asyncio.run(client.transcribe(_wav(tmp_path), context=""))
