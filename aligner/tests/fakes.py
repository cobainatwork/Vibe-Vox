"""測試替身：假對齊器與 wav 產生器，使本服務的測試不需 GPU。"""

import io

import numpy as np
import soundfile as sf

from vibe_vox_aligner.aligner import Word


class FakeAligner:
    """記錄收到的輸入，並回逐字、可預測的時間戳。

    第 i 個字的 start 為 i、end 為 i + 0.5，故測試能以獨立寫出的字面值斷言，
    不必重算實作邏輯。
    """

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[list[tuple[np.ndarray, int]], list[str], list[str]]] = []
        self._error = error

    def align(
        self,
        waveforms: list[tuple[np.ndarray, int]],
        texts: list[str],
        languages: list[str],
    ) -> list[list[Word]]:
        self.calls.append((waveforms, texts, languages))
        if self._error is not None:
            raise self._error
        return [
            [Word(text=ch, start=float(i), end=float(i) + 0.5) for i, ch in enumerate(text)]
            for text in texts
        ]


def wav_bytes(seconds: float = 1.0, sample_rate: int = 24000, channels: int = 1) -> bytes:
    """產生 PCM 16-bit wav。預設 24 kHz 單聲道，對齊 BFF 轉碼後的形狀。"""
    frames = int(seconds * sample_rate)
    shape = (frames,) if channels == 1 else (frames, channels)
    buffer = io.BytesIO()
    sf.write(buffer, np.zeros(shape, dtype=np.float32), sample_rate, format="WAV")
    return buffer.getvalue()
