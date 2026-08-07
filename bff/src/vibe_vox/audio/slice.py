"""正規化 wav 的讀取：時間區間切片與實際長度，供字級強制對齊使用（ADR-0004）。

輸入恆為 AudioIntake.transcoded() 的產出（pcm_s16le 單聲道 wav），格式為內部
不變量，故以 stdlib wave 做 byte 層切片而不再起 ffmpeg 子進程：切片無需重新編碼，
且每段一個子進程會付上逾時處理與檔案落地的代價。全程於記憶體完成，不落暫存檔。
"""

import math
import wave
from dataclasses import dataclass
from pathlib import Path

from vibe_vox.audio.wav import PcmAudio, PcmSpec, wrap_pcm


@dataclass(frozen=True)
class Slice:
    """切出的 wav、其在原音檔涵蓋的時間範圍，以及實際取得的 frame 數。

    start 與 end 是**夾限後的實際範圍**：start 即該切片時間軸的 0 對應原音檔的哪一
    刻，end 為它涵蓋到哪一刻。呼叫端據此把對齊結果換算回絕對時間、並判斷字是否落在
    該段音訊之外，不需自行重算夾限——重算要複製夾限規則與 buffer 設定值，且兩者都取
    frame 格點（見 `slice_wav`），未量化的重算值會與換算後的時間戳落在格點兩側。

    frames 為 0 表示請求的區間完全落在音檔外。此時 wav 仍是合法的 wav（只有
    header），故長度判斷須看 frames 而非 len(wav)。
    """

    wav: bytes
    start: float
    end: float
    frames: int

    @property
    def bounds(self) -> tuple[float, float]:
        """涵蓋的時間範圍，**兩端各向外取到毫秒格點**。

        對齊結果的時間戳取三位小數（與 qwen-asr 的輸出精度一致），而 start／end 落在
        frame 格點上，兩者的格點不同：`240001/24000 = 10.0000417` 這種端點會讓恰好對到
        切片邊界的字在四捨五入後跑到範圍外，使正常段落被落界判準攔下。向外取整讓範圍
        涵蓋所有能捨入到它的時間戳，代價是最多寬 1 毫秒——遠小於任何有意義的偏移。

        這是 `_DURATION_TOLERANCE_SECONDS` 那條教訓的同一個形狀：閾值不能與被量化的
        資料落在同一格點上，否則比較結果由捨入決定而非由語義決定。
        """
        return (math.floor(self.start * 1000) / 1000, math.ceil(self.end * 1000) / 1000)


def wav_duration(src: Path) -> float:
    """音檔的實際總長（秒）。

    不可用 Transcription.duration 代替：那是所有 Segment 的 End 最大值，
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

    frame_count = len(frames) // (params.nchannels * params.sampwidth)
    return Slice(
        wav=wrap_pcm(
            PcmAudio(
                frames,
                PcmSpec(
                    sample_rate=rate,
                    channels=params.nchannels,
                    sample_width=params.sampwidth,
                ),
            )
        ),
        start=first_frame / rate,
        # 自實際讀到的 frame 數導出而非由請求的 end 夾限：readframes 讀到檔尾就停，
        # 兩者在末段會不同，而落界判準要的是真正有音訊的範圍。
        end=(first_frame + frame_count) / rate,
        frames=frame_count,
    )
