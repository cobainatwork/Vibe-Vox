"""離線 stub adapter：供無 GPU 開發與測試使用，回傳固定假結果。"""

from pathlib import Path

from vibe_vox.adapters.base import Segment, TranscriptionResult

# dev / 無 GPU 環境的預設假辨識，讓 UI 在無模型服務時仍可操作。
DEFAULT_STUB_ASR_RESULT = TranscriptionResult(
    segments=[
        Segment(Start=0.0, End=2.0, Speaker="語者 1", Content="（stub 模式假辨識）")
    ],
    raw_text="（stub 模式假辨識）",
    transcription_only="（stub 模式假辨識）",
    duration=2.0,
)


class StubAsrClient:
    def __init__(
        self, ready: bool = True, result: TranscriptionResult | None = None
    ) -> None:
        self._ready = ready
        self._result = result
        self.last_context: str | None = None

    async def health(self) -> bool:
        return self._ready

    async def transcribe(self, audio: Path, *, context: str) -> TranscriptionResult:
        self.last_context = context
        if self._result is None:
            raise RuntimeError("StubAsrClient 未設定 result")
        return self._result


class StubTtsClient:
    def __init__(self, ready: bool = True) -> None:
        self._ready = ready

    async def health(self) -> bool:
        return self._ready
