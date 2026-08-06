"""wav 容器與裸 PCM 的互轉。

消費端契約要 24 kHz／單聲道／16-bit（ADR-0003），而 response_format 同時提供 wav 與
pcm 兩種容器，故「取出 PCM」與「包回 wav」是兩處都要用的動作，集中在此。

取樣率、聲道與位元深度綁成 PcmSpec 而非三個各自旅行的參數：它們永遠一起出現，而
「這段音訊符不符合契約規格」是一次比較而非三次。

不用 ffmpeg：這裡只做容器層的拆裝，不改取樣率、聲道或位元深度，標準庫的 wave 即足夠。
真正的重取樣在 audio/transcode.py。
"""

import io
import wave
from dataclasses import dataclass


class InvalidWav(Exception):
    """位元組不是可解析的 wav 容器。"""


@dataclass(frozen=True)
class PcmSpec:
    """PCM 的取樣規格。預設值為本專案唯一用到的單聲道 16-bit。"""

    sample_rate: int
    channels: int = 1
    sample_width: int = 2


@dataclass(frozen=True)
class PcmAudio:
    """裸 PCM 與其規格。frames 為 little-endian，聲道交錯。"""

    frames: bytes
    spec: PcmSpec


def read_pcm(data: bytes) -> PcmAudio:
    """從 wav 位元組取出 PCM 與其規格。非 wav 或截斷皆拋 InvalidWav。"""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            return PcmAudio(
                frames=w.readframes(w.getnframes()),
                spec=PcmSpec(
                    sample_rate=w.getframerate(),
                    channels=w.getnchannels(),
                    sample_width=w.getsampwidth(),
                ),
            )
    except (wave.Error, EOFError) as exc:
        raise InvalidWav from exc


def wrap_pcm(audio: PcmAudio) -> bytes:
    """把裸 PCM 包成 wav。長度欄位由 wave 正確寫入（非串流，長度已知）。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(audio.spec.channels)
        w.setsampwidth(audio.spec.sample_width)
        w.setframerate(audio.spec.sample_rate)
        w.writeframes(audio.frames)
    return buf.getvalue()
