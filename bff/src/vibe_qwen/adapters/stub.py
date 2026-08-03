"""離線 stub adapter：供無 GPU 開發與測試使用，回傳固定假結果。"""

from pathlib import Path

from vibe_qwen.adapters.base import TranscriptionResult


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
