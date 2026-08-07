"""真模型串接：VllmAsrClient 經官方 vLLM plugin serve 的 VibeVoice-ASR。

契約對齊 microsoft/VibeVoice 官方 vllm_plugin（scripts/gradio_asr_demo_api_video.py）：
- 端點 /v1/chat/completions；音檔以 `audio_url` 的 data URL 傳入（非 input_audio）。
- prompt 明確要求輸出 Start/End/Speaker/Content 四個 key，並附音檔秒數；
  hotword/context 接在 "Context information (...)" 之後。
- serve 端 `--served-model-name vibevoice`，故 client 的 model 參數用該 served name。
- 回傳為含 Start/End/Speaker/Content 的結構化 segments（dict 的 segments 或直接 array）。

遠端連線屬環境相依，測試以 httpx MockTransport 注入假回應。
"""

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

import httpx

from vibe_vox.adapters.base import (
    AsrTimeout,
    AsrUnavailable,
    Segment,
    AsrResult,
)
from vibe_vox.adapters.zh import to_traditional

_SHOW_KEYS = "Start time, End time, Speaker ID, Content"

# ffprobe 只讀檔頭的 metadata，正常在一秒內完成；30 秒是留給慢速磁碟與 200 MB 級
# 檔案的餘裕。刻意遠小於 ffmpeg_timeout（60）與 asr_timeout（300）：這一步掛住不該
# 吃掉整個請求的預算。不進 config：它不是部署會調的值。
_FFPROBE_TIMEOUT_SECONDS = 30.0


def _instruction(duration: float, context: str) -> str:
    """官方 prompt：欄位描述與 hotword 措辭皆對齊訓練格式。

    權威來源為 processor（vibevoice/processor/vibevoice_asr_processor.py 的
    `# Build token sequence following training format`）——LoRA 微調腳本即以它組
    輸入，故模型只在訓練中看過這組措辭。注意欄位描述（Start time…）與模型輸出的
    JSON key（Start…）本就不同，官方刻意如此。
    """
    if context:
        base = (
            f"This is a {duration:.2f} seconds audio, with extra info: {context}\n\n"
            f"Please transcribe it with these keys: {_SHOW_KEYS}"
        )
    else:
        base = (
            f"This is a {duration:.2f} seconds audio, please transcribe it "
            f"with these keys: {_SHOW_KEYS}"
        )
    base += (
        "\nImportant: You must output the transcription strictly in "
        "Traditional Chinese (繁體中文)."
    )
    return base


async def _audio_duration(path: Path) -> float:
    """以 ffprobe 取音檔秒數（對齊官方 test_api.py），支援各格式；失敗回退 1.0，
    避免 duration 異常使 max_tokens 過小截斷內容或 prompt 秒數不實。

    以 asyncio 子進程而非 `subprocess.check_output`：後者同步阻塞 event loop，會使
    guard 的 `asyncio.timeout` 無法觸發：ffprobe 掛住時整個請求只剩反向代理能收尾，
    使用者拿到 HTML 錯誤頁，而那正是 #35 要消除的結果。逾時亦不可省，`check_output`
    原本連 `timeout=` 都沒有。
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return 1.0  # ffprobe 不在 PATH（開發機、精簡 image）：回退而非讓請求失敗
    try:
        async with asyncio.timeout(_FFPROBE_TIMEOUT_SECONDS):
            out, _ = await proc.communicate()
    except (TimeoutError, asyncio.CancelledError) as exc:
        proc.kill()
        await proc.wait()
        if isinstance(exc, asyncio.CancelledError):
            raise  # 連線中斷要繼續向上傳播，與 transcode 一致
        return 1.0
    if proc.returncode != 0:
        return 1.0
    try:
        return float(out.decode().strip())
    except ValueError:
        return 1.0


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


def _first_value(seg: dict, *keys: str, default: Any = None) -> Any:
    """取第一個有值的 key。模型有時拿 prompt 的欄位描述（Start time…）當 JSON key，
    官方 gradio demo 亦做同樣的三重相容；否則時間戳會靜默變 0。"""
    for key in keys:
        value = seg.get(key)
        if value is not None:
            return value
    return default


def _parse(content: str) -> AsrResult:
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
                Start=_as_float(_first_value(s, "Start", "start", "Start time")),
                End=_as_float(_first_value(s, "End", "end", "End time")),
                Speaker=to_traditional(
                    str(_first_value(s, "Speaker", "speaker", "Speaker ID", default=""))
                ),
                Content=to_traditional(
                    str(_first_value(s, "Content", "content", "text", default=""))
                ),
            )
        )

    return AsrResult(
        segments=segments,
        raw_text=content,
        transcription_only=(
            "".join(s.Content for s in segments) if segments else to_traditional(content)
        ),
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

    async def transcribe(self, audio: Path, *, context: str) -> AsrResult:
        audio_b64 = base64.b64encode(audio.read_bytes()).decode("ascii")
        data_url = f"data:audio/wav;base64,{audio_b64}"
        duration = await _audio_duration(audio)
        prompt = _instruction(duration, context)
        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a helpful assistant that transcribes audio "
                        "input into text output in JSON format."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "audio_url", "audio_url": {"url": data_url}},
                        {"type": "text", "text": prompt},
                    ],
                },
            ],
            # 對齊官方 vllm_plugin（tests/test_api.py、gradio demo）：greedy 解碼、
            # top_p 1.0、max_tokens 上限。repetition_penalty 與依秒數的動態 max_tokens
            # 抑制官方已知的 repetition/hallucination 迴圈（見 test_api_auto_recover.py）。
            "temperature": 0,
            "top_p": 1.0,
            "repetition_penalty": 1.1,
            "max_tokens": int(duration * 10) + 100,
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
