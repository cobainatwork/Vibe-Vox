"""一次辨識：音檔輸入 → 辨識 → 字級對齊 → 合理性檢查（ADR-0004 的鏈路）。

ADR-0004 把這條鏈路畫成一條線，但沒指定誰擁有它，於是它落在 HTTP 端點的縮排裡。
代價是 #34／#35／#36／#37 各自裂成兩三處：判準在 `alignment`、分批在
`adapters/aligner`、預算在 `config`，而每一個都得同時再動端點一次。

本 module 就是那個擁有者。它持有的不只是步驟順序，還有兩者之間的**時序約束**：
`audio_duration` 必須在 intake 的 context 內取得（離開時 wav 即刪除），而彙總發生在
context 外。以前那條約束只由縮排表達，沒有型別或介面說出它。

不含 HTTP 的關注點（multipart、併發護欄、錯誤信封）與 Context prompt 的編譯——後者
跨 Hotword 領域且另有呼叫端（管理平面的預覽），見 `api/asr.py`。
"""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from vibe_vox.adapters.base import AlignerClient, AsrClient
from vibe_vox.alignment import AlignedSegment, AlignmentSummary, merge_alignment
from vibe_vox.audio.slice import wav_duration


@runtime_checkable
class AudioSource(Protocol):
    """把上傳的位元組化為一個可讀的正規化 wav，用畢即回收。

    只描述 `Transcriber` 實際用到的那一個方法，而非整個 `AudioIntake`：呼叫端要知道
    的就是這些。**wav 只在 context 內有效**——離開時檔案即刪除，故任何需要讀它的步驟
    都必須在 context 內完成。
    """

    def transcoded(
        self, chunks: AsyncIterator[bytes], *, sample_rate: int
    ) -> AbstractAsyncContextManager[Path]:
        ...


class Transcription(BaseModel):
    """一次辨識的完整結果，形狀為消費端契約約束（docs/api/asr.md §4）。

    `duration` 是所有 Segment 的 End 最大值，**其值隨對齊改變**：段界對齊後重算為末字
    的 End。它與 `alignment.audio_duration`（音檔實際總長）是不同的量，兩者的差就是
    結尾沉默。

    `applied_context` 不在此：那是端點層依 Hotword 編出來的，屬另一個關注點。
    """

    segments: list[AlignedSegment]
    raw_text: str
    transcription_only: str
    duration: float
    alignment: AlignmentSummary


class Transcriber:
    """擁有一次辨識的順序與時序約束。

    依賴由建構時注入而非在內部建立，故整條鏈路可在 HTTP 之外、無 GPU 也無 ffmpeg 的
    情況下驗證（見 test_transcription.py）。
    """

    def __init__(
        self,
        *,
        intake: AudioSource,
        asr: AsrClient,
        aligner: AlignerClient,
        sample_rate: int,
    ) -> None:
        self._intake = intake
        self._asr = asr
        self._aligner = aligner
        self._sample_rate = sample_rate

    async def transcribe(
        self, chunks: AsyncIterator[bytes], *, context: str
    ) -> Transcription:
        """辨識一份上傳的音檔，回帶字級時間戳與彙總數字的結果。

        對齊不可得不會使本方法失敗：那由 `AlignerClient.align` 就地降級（ADR-0004 的
        第二層），逐字稿照常回傳、全段標記未對齊。
        """
        async with self._intake.transcoded(
            chunks, sample_rate=self._sample_rate
        ) as wav:
            result = await self._asr.transcribe(wav, context=context)
            # 必須在 context 內：離開時 wav 即刪除，而下面的彙總在 context 外。
            audio_duration = wav_duration(wav)
            alignments = await self._aligner.align(wav, result.segments)

        segments, alignment = merge_alignment(
            result.segments, alignments, audio_duration=audio_duration
        )
        return Transcription(
            segments=segments,
            raw_text=result.raw_text,
            transcription_only=result.transcription_only,
            duration=max((s.End for s in segments), default=0.0),
            alignment=alignment,
        )
