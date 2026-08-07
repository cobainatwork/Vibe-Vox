"""離線 stub adapter：供無 GPU 開發與測試使用，回傳固定假結果。"""

from pathlib import Path

from vibe_vox.adapters.base import (
    CONTRACT_SPEC,
    Segment,
    SegmentAlignment,
    AsrResult,
    Utterance,
)
from vibe_vox.audio.wav import PcmAudio

# dev / 無 GPU 環境的預設假辨識，讓 UI 在無模型服務時仍可操作。
DEFAULT_STUB_ASR_RESULT = AsrResult(
    segments=[
        Segment(Start=0.0, End=2.0, Speaker="語者 1", Content="（stub 模式假辨識）")
    ],
    raw_text="（stub 模式假辨識）",
    transcription_only="（stub 模式假辨識）",
)


class StubAsrClient:
    def __init__(
        self, ready: bool = True, result: AsrResult | None = None
    ) -> None:
        self._ready = ready
        self._result = result
        self.last_context: str | None = None

    async def health(self) -> bool:
        return self._ready

    async def transcribe(self, audio: Path, *, context: str) -> AsrResult:
        self.last_context = context
        if self._result is None:
            raise RuntimeError("StubAsrClient 未設定 result")
        return self._result


class StubAlignerClient:
    """離線替身。預設回空清單，即全段標記未對齊。

    **不模擬成功對齊**：假時間戳會讓 dev 環境看起來對齊正常，掩蓋真實服務未接上
    的事實。要驗證對齊路徑請跑真 aligner 的 CPU 模式（`VIBE_VOX_ALIGNER_DEVICE=cpu`，
    輸出與 GPU 逐字相同，見 `aligner/README.md`），不需要 GPU。
    """

    def __init__(
        self, ready: bool = True, result: list[SegmentAlignment] | None = None
    ) -> None:
        self._ready = ready
        self._result = result

    async def health(self) -> bool:
        return self._ready

    async def align(
        self, audio: Path, segments: list[Segment]
    ) -> list[SegmentAlignment]:
        if self._result is None:
            # bounds 取段界本身：本替身不切片，故沒有 buffer 可言。words 為空時落界
            # 判準用不到它，但 bounds 屬 interface，不能給假的寬範圍。
            return [
                SegmentAlignment(words=[], bounds=(s.Start, s.End)) for s in segments
            ]
        return self._result


def _silence(seconds: float) -> bytes:
    """指定長度的靜音 PCM，規格為消費端契約的 24 kHz／單聲道／16-bit。"""
    return b"\x00\x00" * int(seconds * CONTRACT_SPEC.sample_rate)


class StubTtsClient:
    """離線替身。回與消費端契約同規格的靜音 wav，長度依字數粗估。

    **回靜音而非假語音**：dev 環境聽到靜音就知道模型服務沒接上；回一段像樣的
    語音會讓人以為合成正常，掩蓋真實服務未就緒的事實（同 StubAlignerClient 的
    理由）。長度隨字數變化則是為了讓前端的播放器與下載能被實際操作。
    """

    def __init__(self, ready: bool = True) -> None:
        self._ready = ready
        self.last_utterances: list[Utterance] | None = None
        self.last_reference_audio: Path | None = None

    async def health(self) -> bool:
        return self._ready

    async def synthesize(
        self, utterances: list[Utterance], *, reference_audio: Path
    ) -> PcmAudio:
        self.last_utterances = utterances
        self.last_reference_audio = reference_audio
        # 逐句產生再串接，與真 adapter 的形狀一致：把 N 句摺成單一 buffer 的話，
        # 多句路徑（串接規則、標頭只留一份）在測試裡永遠不會被走到。
        frames = b"".join(_silence(max(0.5, len(u.text) * 0.2)) for u in utterances)
        return PcmAudio(frames, CONTRACT_SPEC)
