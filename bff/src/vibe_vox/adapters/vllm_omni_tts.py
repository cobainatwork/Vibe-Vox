"""真模型串接：VllmOmniTtsClient 經 vLLM-Omni 的 /v1/audio/speech 服務 VoxCPM2。

契約以逐行讀 vllm-omni 原始碼取證（見
docs/superpowers/specs/2026-08-05-voxcpm2-serving-transport.md），三項與直覺相反：

- **不送 ref_text。** 給了 ref_text 會落到 continuation（Hi-Fi）模式，行內風格失效。
  只給 ref_audio 才是 reference（Controllable）模式，兩型音色一律走這條（#16）。
- **不送 instructions 與 task_type。** 兩者對 VoxCPM2 從未被讀取且不報錯，帶了會回
  200 加一段沒套用該風格的音訊。風格的唯一通道是 input 的行內 (...) 前綴。
- **voice 恆為 "default"。** VoxCPM2 沒有內建語者，該欄位是 OpenAI schema 的必填項
  但模型語意上忽略它；音色身分完全由 ref_audio 決定。
"""

import asyncio
import base64
from pathlib import Path

import httpx

from vibe_vox.adapters.base import (
    CONTRACT_SPEC,
    TtsTimeout,
    TtsUnavailable,
    Utterance,
)
from vibe_vox.audio.errors import TranscodeError, TranscodeTimeout
from vibe_vox.audio.sniff import HEADER_BYTES, detect_audio_format
from vibe_vox.audio.transcode import resample_wav_to_pcm
from vibe_vox.audio.wav import InvalidWav, PcmAudio, read_pcm, wrap_pcm

# OpenAI schema 的必填欄位，VoxCPM2 語意上忽略（沒有內建語者）。
_VOICE_PLACEHOLDER = "default"

# 參考音原樣落地、檔名是 uuid 無副檔名（api/admin_voices.py），故容器型別只能從檔頭嗅。
# 寫死 audio/wav 會讓 mp3 音色的 data URI 謊報容器；嗅不出來時不猜，交給端點自己判斷。
_AUDIO_MIME = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "flac": "audio/flac",
    "ogg": "audio/ogg",
    "m4a": "audio/mp4",
    "webm": "audio/webm",
}
_FALLBACK_MIME = "application/octet-stream"


def _concat_pcm(parts: list[PcmAudio]) -> PcmAudio:
    """把逐句的 PCM 串成一段。

    各句規格必然相同（同一端點、同一組參數），故沿用第一段的規格。空清單回契約規格
    的零長度音訊，讓「沒有句子」與「合成失敗」在型別上分得開。
    """
    if not parts:
        return PcmAudio(b"", CONTRACT_SPEC)
    return PcmAudio(
        frames=b"".join(p.frames for p in parts),
        spec=parts[0].spec,
    )


def _styled_text(u: Utterance) -> str:
    """把發聲方式組成行內前綴，語法對齊官方 CLI 的 build_final_text。

    直接拼接是安全的：控制語法的中性化由 Utterance 自己保證（見 adapters/base.py），
    不是靠某個呼叫端記得先做。
    """
    if not u.instruct:
        return u.text
    return f"({u.instruct}){u.text}"


def _data_url(path: Path) -> str:
    """參考音以 data: base64 傳，不用 file://。

    file:// 需要 server 開 --allowed-local-media-path，會讓 vLLM 容器能讀取我方掛載
    的任意路徑；data: 沒有這個暴露面。

    **不驗參考音時長**：那是 Voice 建立時的不變量（audio/reference.py），驗一次就夠，
    放在此處等於每次合成都算一遍，而那時已經沒有人能修正它。

    **同步函式，呼叫端負責把它移出 event loop。** 讀檔與 base64 編碼都是同步且與檔案大小
    成正比的工作，跑在 loop 上會讓整個 BFF（含 /api/health 與所有 ASR 請求）停住那段時間。
    """
    raw = path.read_bytes()
    container = detect_audio_format(raw[:HEADER_BYTES])
    mime = _AUDIO_MIME.get(container or "", _FALLBACK_MIME)
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


class VllmOmniTtsClient:
    def __init__(
        self,
        base_url: str,
        served_model_name: str,
        *,
        timeout: float = 120.0,
        ffmpeg_timeout_seconds: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url
        self._model = served_model_name
        self._timeout = timeout
        self._ffmpeg_timeout = ffmpeg_timeout_seconds
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

    async def synthesize(
        self, utterances: list[Utterance], *, reference_audio: Path
    ) -> PcmAudio:
        try:
            ref_audio = await asyncio.to_thread(_data_url, reference_audio)
        except OSError as exc:
            # 可讀是呼叫端的前置條件，此處只處理端點檢查與這次讀檔之間的時間差；
            # 為何要翻譯而不讓它逸出，見 base.py 的 TtsClient.synthesize。
            raise TtsUnavailable from exc
        parts: list[PcmAudio] = []
        try:
            async with self._client() as client:
                for u in utterances:
                    payload = {
                        "model": self._model,
                        "input": _styled_text(u),
                        "voice": _VOICE_PLACEHOLDER,
                        "response_format": "wav",
                        "ref_audio": ref_audio,
                    }
                    resp = await client.post("/v1/audio/speech", json=payload)
                    resp.raise_for_status()
                    parts.append(read_pcm(resp.content))
        except httpx.TimeoutException as exc:
            raise TtsTimeout from exc
        except httpx.HTTPError as exc:
            raise TtsUnavailable from exc
        except InvalidWav as exc:
            # 端點對超界參考音等狀況回的是 ValueError 的文字，不是音訊。
            raise TtsUnavailable from exc
        return await self._to_contract_spec(_concat_pcm(parts))

    async def _to_contract_spec(self, audio: PcmAudio) -> PcmAudio:
        """重取樣為契約規格（24 kHz／單聲道／16-bit）。

        端點回 48 kHz，故正常路徑必然要重取樣；已符合規格時原樣放行，省掉一次無謂的
        重編碼與子進程往返。ffmpeg 要一個帶標頭的來源才知道輸入規格，故只在真的要轉時
        才包 wav。
        """
        if audio.spec == CONTRACT_SPEC:
            return audio
        try:
            frames = await resample_wav_to_pcm(
                wrap_pcm(audio),
                sample_rate=CONTRACT_SPEC.sample_rate,
                channels=CONTRACT_SPEC.channels,
                timeout_seconds=self._ffmpeg_timeout,
            )
        except TranscodeTimeout as exc:
            # 轉碼是我方的處理步驟，對消費端而言與模型逾時同樣是「這次沒完成」；
            # 不冒 TRANSCODE_TIMEOUT，那個碼在契約裡屬於使用者上傳的音檔。
            raise TtsTimeout from exc
        except TranscodeError as exc:
            raise TtsUnavailable from exc
        return PcmAudio(frames, CONTRACT_SPEC)
