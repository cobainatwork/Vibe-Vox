"""正規化 wav 的讀取：時間區間切片與實際長度，供字級強制對齊使用（ADR-0004）。

輸入恆為 AudioIntake.transcoded() 的產出（pcm_s16le 單聲道 wav），格式為內部
不變量，故以 stdlib wave 做 byte 層切片而不再起 ffmpeg 子進程：切片無需重新編碼，
且每段一個子進程會付上逾時處理與檔案落地的代價。全程於記憶體完成，不落暫存檔。
"""

import io
import wave
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Slice:
    """切出的 wav、其在原音檔的起始秒數，以及實際取得的 frame 數。

    start 是**夾限後的實際切片起點**，即該切片時間軸的 0 對應原音檔的哪一刻。
    呼叫端據此把對齊結果換算回絕對時間，不需自行重算夾限。

    frames 為 0 表示請求的區間完全落在音檔外。此時 wav 仍是合法的 wav（只有
    header），故長度判斷須看 frames 而非 len(wav)。
    """

    wav: bytes
    start: float
    frames: int


def wav_duration(src: Path) -> float:
    """音檔的實際總長（秒）。

    不可用 TranscriptionResult.duration 代替：那是所有 Segment 的 End 最大值，
    尾端靜音不計入（docs/api/asr.md §4.2），而 alignment.audio_duration 要的是
    實際長度——結尾沉默時長正是由兩者的差算出。
    """
    with wave.open(str(src), "rb") as reader:
        return reader.getnframes() / reader.getframerate()


def slice_wav(src: Path, *, start: float, end: float) -> Slice:
    """取 [start, end) 的音訊，回傳獨立的 wav bytes 與實際起點。"""
    with wave.open(str(src), "rb") as reader:
        params = reader.getparams()
        rate = params.framerate
        # 夾限至 [0, 檔尾]：起點為負（第一段減 buffer）或落在音檔外（模型時間戳
        # 幻覺）時 setpos 會拋 wave.Error，讓低階例外冒成 500。夾限後者得空切片，
        # 由呼叫端既有的降級路徑處理。
        first_frame = min(max(int(start * rate), 0), params.nframes)
        reader.setpos(first_frame)
        frames = reader.readframes(int(end * rate) - first_frame)

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(params.nchannels)
        writer.setsampwidth(params.sampwidth)
        writer.setframerate(rate)
        writer.writeframes(frames)
    return Slice(
        wav=buffer.getvalue(),
        start=first_frame / rate,
        frames=len(frames) // (params.nchannels * params.sampwidth),
    )
