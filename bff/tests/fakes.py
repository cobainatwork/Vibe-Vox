"""測試共用的替身。

放在此處而非各測試檔內：`FakeIntake` 要正確模擬「離開 context 即刪檔」這條時序契約
（`audio/intake.py` 的 `transcoded`），而那正是 `transcription.Transcriber` 存在的理由
之一。兩份實作一旦漂移，其中一份就會悄悄不再守護那條約束。
"""

import wave
from contextlib import asynccontextmanager
from pathlib import Path


class FakeIntake:
    """消耗 chunks、yield 真 wav path，避開 ffmpeg。

    產出真檔而非不存在的路徑：辨識鏈路要讀音檔的實際長度（`alignment.audio_duration`），
    而該值不能以 Segment 的 End 最大值代替（docs/api/asr.md §4.2）。

    離開 context 時刪檔，與 `AudioIntake.transcoded` 一致——任何需要讀 wav 的步驟都必須
    在 context 內完成，替身若不刪檔就測不出違反這條約束的改動。
    """

    def __init__(self, directory: Path, seconds: float = 2.0) -> None:
        self._directory = directory
        self._seconds = seconds

    @asynccontextmanager
    async def transcoded(self, chunks, *, sample_rate, channels=1):
        async for _ in chunks:
            pass
        path = self._directory / "fake.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(channels)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(b"\x00\x00" * int(self._seconds * sample_rate))
        try:
            yield path
        finally:
            path.unlink(missing_ok=True)
