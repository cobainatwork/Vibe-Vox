"""AsrClient/TtsClient/AlignerClient 介面（ADR-0001 的唯一 stub 邊界）。

#1 walking skeleton 僅需就緒探測 health()。辨識 transcribe()（#5）與合成
synthesize()（其對應票）隨各票加入本介面。字級對齊 align()（#27）為 ADR-0004
新增的第三個邊界，其實作為獨立部署單元而非模型 in-process 載入。
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


class Word(BaseModel):
    """Forced alignment 產生時間戳的最小單位，秒（CONTEXT.md 的 Word 詞條）。

    中文為單一漢字而非語意上的詞，且標點與符號不產生 Word，故一段的 Word 數量
    不等於其 Content 的字元數。欄位大寫以對齊 Segment 與消費端契約
    （docs/api/asr.md §4.4）。
    """

    Text: str
    Start: float
    End: float


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
class AlignerClient(Protocol):
    async def health(self) -> bool:
        """回報對齊服務是否就緒。"""
        ...

    async def align(
        self, audio: Path, segments: list[Segment]
    ) -> list[list[Word]]:
        """逐段對齊，回每段的字級時間戳，順序與 segments 一一對應。

        時間戳為**原音檔的絕對時間**（切片 offset 已加回）。對齊品質的合理性
        檢查與降級標記不在此層，屬 #28。
        """
        ...


@runtime_checkable
class TtsClient(Protocol):
    async def health(self) -> bool:
        """回報 TTS 模型服務是否就緒。"""
        ...
