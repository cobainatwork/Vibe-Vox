"""真模型串接 VllmAsrClient：seam 為 httpx 傳輸層（MockTransport 注入假 vLLM 回應）。

真的連遠端 vLLM 屬環境相依，不進測試；此處驗證組請求、解析回應、遠端錯誤映射的全部邏輯。
"""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from vibe_qwen.adapters.vllm_asr import AsrTimeout, AsrUnavailable, VllmAsrClient


def _wav(tmp_path) -> Path:
    p = tmp_path / "a.wav"
    p.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
    return p


def _reply(content: str) -> httpx.Response:
    return httpx.Response(
        200, json={"choices": [{"message": {"content": content}}]}
    )


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
    parts = body["messages"][-1]["content"]
    assert any(p.get("type") == "input_audio" for p in parts)  # base64 音檔在其中
    assert any(
        p.get("type") == "text" and "台積電" in p.get("text", "") for p in parts
    )  # Hotword context 併入指示

    assert len(result.segments) == 1
    assert result.segments[0].Speaker == "A"
    assert result.segments[0].Content == "你好"
    assert result.transcription_only == "你好"


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
