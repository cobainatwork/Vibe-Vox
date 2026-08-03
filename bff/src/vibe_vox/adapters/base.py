"""AsrClient/TtsClient 介面（ADR-0001 的唯一 stub 邊界）。

#1 walking skeleton 僅需就緒探測 health()。辨識 transcribe()（#5）與合成
synthesize()（其對應票）隨各票加入本介面。
"""

from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class Segment(BaseModel):
    """ASR 輸出的一個區塊（Who/When/What），欄位形狀為消費端契約約束。

    非語句單位：VibeVoice-ASR 為窮盡連續切分，段界是模型自選的切點而非語音
    邊界，相鄰段的 End 與下一段 Start 幾乎總是相同。詳見 CONTEXT.md 的
    Segment 詞條與 docs/api/asr.md §4.3。
    """

    Start: float
    End: float
    Speaker: str
    Content: str


class TranscriptionResult(BaseModel):
    """一次辨識的完整結果。applied_context 由端點層另行附加，不屬本結果。"""

    segments: list[Segment]
    raw_text: str
    transcription_only: str
    duration: float


@runtime_checkable
class AsrClient(Protocol):
    async def health(self) -> bool:
        """回報 ASR 模型服務是否就緒。"""
        ...

    async def transcribe(self, audio: Path, *, context: str) -> TranscriptionResult:
        """辨識正規化後的 wav，回帶語者與時間戳的分段結果。"""
        ...


@runtime_checkable
class TtsClient(Protocol):
    async def health(self) -> bool:
        """回報 TTS 模型服務是否就緒。"""
        ...
