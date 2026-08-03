"""真模型串接：VllmAsrClient 經官方 vLLM plugin serve 的 VibeVoice-ASR。

契約對齊 microsoft/VibeVoice 官方 vllm_plugin（scripts/gradio_asr_demo_api_video.py）：
- 端點 /v1/chat/completions；音檔以 `audio_url` 的 data URL 傳入（非 input_audio）。
- prompt 明確要求輸出 Start/End/Speaker/Content 四個 key，並附音檔秒數；
  hotword/context 接在 "Context information (...)" 之後。
- serve 端 `--served-model-name vibevoice`，故 client 的 model 參數用該 served name。
- 回傳為含 Start/End/Speaker/Content 的結構化 segments（dict 的 segments 或直接 array）。

遠端連線屬環境相依，測試以 httpx MockTransport 注入假回應。
"""

import base64
import json
import wave
from pathlib import Path
from typing import Any

import httpx

from vibe_qwen.adapters.base import Segment, TranscriptionResult
from vibe_qwen.adapters.zh import to_traditional


class AsrUnavailable(Exception):
    """遠端 ASR 連不上、回錯或回傳信封異常（端點層映射 → 502）。"""


class AsrTimeout(Exception):
    """遠端 ASR 呼叫逾時（端點層映射 → 504）。"""


def _instruction(duration: float, context: str) -> str:
    """官方 prompt：要求輸出四個 key，附音檔秒數；context 併於背景資訊區。"""
    base = (
        f"This is a {duration:.2f} seconds audio, please transcribe it "
        "with these keys: Start, End, Speaker, Content"
    )
    if context:
        base += "\n\nContext information (hotwords, speaker names, etc.):\n" + context
    return base


def _wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as w:
            rate = w.getframerate()
            return w.getnframes() / float(rate) if rate else 0.0
    except (wave.Error, OSError, ValueError):
        return 0.0


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

    官方輸出為含 Start/End/Speaker/Content 的 segments，可能是 `{segments:[...]}`
    或直接的 array，兩者都接。
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = None

    if isinstance(data, dict):
        raw_segments = data.get("segments", [])
    elif isinstance(data, list):
        raw_segments = data
    else:
        raw_segments = []

    segments: list[Segment] = []
    for s in raw_segments:
        if not isinstance(s, dict):
            continue
        segments.append(
            Segment(
                Start=_as_float(s.get("Start")),
                End=_as_float(s.get("End")),
                Speaker=to_traditional(str(s.get("Speaker", ""))),
                Content=to_traditional(str(s.get("Content", ""))),
            )
        )

    return TranscriptionResult(
        segments=segments,
        raw_text=content,
        transcription_only=(
            "".join(s.Content for s in segments) if segments else to_traditional(content)
        ),
        duration=max((s.End for s in segments), default=0.0),
    )


class VllmAsrClient:
    def __init__(
        self,
        base_url: str,
        served_model_name: str,
        *,
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url
        self._model = served_model_name
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
        data_url = f"data:audio/wav;base64,{audio_b64}"
        prompt = _instruction(_wav_duration(audio), context)
        payload = {
            "model": self._model,
            # ASR 為確定性任務，需 greedy 解碼；不指定則 vLLM 用隨機取樣，
            # 會在低信心片段吐出訓練語料的他語 token（俄/韓等亂碼）。
            "temperature": 0,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "audio_url", "audio_url": {"url": data_url}},
                        {"type": "text", "text": prompt},
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
