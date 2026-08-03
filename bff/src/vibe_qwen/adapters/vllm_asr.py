"""真模型串接：VllmAsrClient 經 vLLM 的 OpenAI 相容 chat completions 呼叫 VibeVoice-ASR。

音檔以 base64 `input_audio` content part 傳入（vLLM 多模態格式），context 併入
文字指示。回傳的 Who/When/What 由 message.content 解析。

VibeVoice-ASR 的確切輸出 shape 屬模型特定、未於無環境下驗證：`_parse` 以「含
segments 的 JSON 物件」為假設，對非 JSON／缺欄位防禦性 fallback（不 500）。信封層
異常（choices 缺失、content 非字串）視為上游不可用 → AsrUnavailable。遠端連線屬
環境相依，測試以 httpx MockTransport 注入假回應。
"""

import base64
import json
from pathlib import Path
from typing import Any

import httpx

from vibe_qwen.adapters.base import Segment, TranscriptionResult


class AsrUnavailable(Exception):
    """遠端 ASR 連不上、回錯或回傳信封異常（端點層映射 → 502）。"""


class AsrTimeout(Exception):
    """遠端 ASR 呼叫逾時（端點層映射 → 504）。"""


_BASE_INSTRUCTION = "請辨識這段音訊，輸出帶語者與時間戳的分段結果。"


def _instruction(context: str) -> str:
    return f"{_BASE_INSTRUCTION}\n\n參考詞彙：{context}" if context else _BASE_INSTRUCTION


def _extract_content(data: Any) -> str:
    """從 vLLM 回應信封取出 message.content；信封異常視為上游不可用。"""
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AsrUnavailable from exc
    if not isinstance(content, str):
        raise AsrUnavailable
    return content


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse(content: str) -> TranscriptionResult:
    """防禦性解析模型輸出：非 JSON 或缺欄位皆不崩潰，退回純文字。

    transcription_only 的串接方式（此處以空字串連接）依賴模型輸出的分段語意，
    真實環境接上 VibeVoice-ASR 後再對齊。
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = None

    segments: list[Segment] = []
    if isinstance(data, dict):
        for s in data.get("segments", []):
            if not isinstance(s, dict):
                continue
            segments.append(
                Segment(
                    Start=_as_float(s.get("Start")),
                    End=_as_float(s.get("End")),
                    Speaker=str(s.get("Speaker", "")),
                    Content=str(s.get("Content", "")),
                )
            )

    return TranscriptionResult(
        segments=segments,
        raw_text=content,
        transcription_only="".join(s.Content for s in segments) if segments else content,
        duration=max((s.End for s in segments), default=0.0),
    )


class VllmAsrClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url
        self._model = model
        self._timeout = timeout
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url, timeout=self._timeout, transport=self._transport
        )

    async def health(self) -> bool:
        try:
            async with self._client() as client:
                resp = await client.get("/health")
                return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def transcribe(self, audio: Path, *, context: str) -> TranscriptionResult:
        audio_b64 = base64.b64encode(audio.read_bytes()).decode("ascii")
        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _instruction(context)},
                        {
                            "type": "input_audio",
                            "input_audio": {"data": audio_b64, "format": "wav"},
                        },
                    ],
                }
            ],
        }
        try:
            async with self._client() as client:
                resp = await client.post("/v1/chat/completions", json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.TimeoutException as exc:
            raise AsrTimeout from exc
        except httpx.HTTPError as exc:
            raise AsrUnavailable from exc
        return _parse(_extract_content(data))
