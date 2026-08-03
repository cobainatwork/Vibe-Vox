"""串流落地、驗證與轉碼 facade（設計 §2.2、§7）。

save_upload 逐塊將上傳串流寫入伺服器生成的 UUID 暫存檔，先累積小型 header window
判容器型別、超過 max_bytes 即止，全程不整檔載入記憶體。AudioIntake.transcoded() 為
#5／#7 的共用進入點，串接 save_upload → transcode 並於用畢清檔。
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from vibe_vox.audio.errors import FileTooLarge, UnsupportedAudioFormat
from vibe_vox.audio.sniff import detect_audio_format
from vibe_vox.audio.transcode import transcode_to_wav

# 足以涵蓋所有允許格式的 magic（皆落在檔首前 12 bytes），遠小於整檔。
_HEADER_WINDOW = 64


async def save_upload(
    chunks: AsyncIterator[bytes], *, temp_dir: Path, max_bytes: int
) -> Path:
    """串流寫入 UUID 暫存檔，回傳其路徑。

    不符允許容器 → UnsupportedAudioFormat（寫入前即拒）；累計超過 max_bytes →
    FileTooLarge（超限即止、清除部分檔）。檔名為伺服器 UUID，不含使用者輸入。
    """
    temp_dir = Path(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)

    stream = aiter(chunks)

    # 先累積 header window（或串流提前結束）再判型別，避免首塊過短誤判；
    # 同時對累積量套 max_bytes，極端大的首塊也及早擋下、不入記憶體。
    header = bytearray()
    while len(header) < _HEADER_WINDOW:
        try:
            header.extend(await anext(stream))
        except StopAsyncIteration:
            break
        if len(header) > max_bytes:
            raise FileTooLarge()

    if detect_audio_format(bytes(header)) is None:
        raise UnsupportedAudioFormat()

    path = temp_dir / uuid.uuid4().hex
    # O_EXCL 原子建立；若目標已存在（碰撞/symlink）則 FileExistsError，不清除他人檔。
    fh = open(path, "xb")
    with fh:
        try:
            fh.write(header)  # header ≤ max_bytes（上方迴圈已保證）
            total = len(header)
            async for chunk in stream:
                total += len(chunk)
                if total > max_bytes:
                    raise FileTooLarge()
                fh.write(chunk)
        except BaseException:
            fh.close()  # Windows 需先關檔才能 unlink
            path.unlink(missing_ok=True)
            raise
    return path


class AudioIntake:
    """#5／#7 的唯一共用進入點：串流上傳 → 轉碼 → 用畢自動清檔（設計 §2.2、§7）。

    以 Settings 綁定資源邊界（temp_dir／max_bytes／逾時／ffmpeg 路徑），呼叫端只需
    每次指定目標 sample_rate。
    """

    def __init__(
        self,
        *,
        temp_dir: Path,
        max_bytes: int,
        timeout_seconds: float,
        ffmpeg: str = "ffmpeg",
    ) -> None:
        self._temp_dir = Path(temp_dir)
        self._max_bytes = max_bytes
        self._timeout_seconds = timeout_seconds
        self._ffmpeg = ffmpeg

    @asynccontextmanager
    async def transcoded(
        self, chunks: AsyncIterator[bytes], *, sample_rate: int, channels: int = 1
    ):
        """yield 正規化後的 wav 路徑；離開 context 時原始暫存檔與輸出 wav 皆刪除。

        逾時或連線中斷（CancelledError）時，仍於 finally 清檔並殺子進程（見 transcode）。
        """
        raw = await save_upload(
            chunks, temp_dir=self._temp_dir, max_bytes=self._max_bytes
        )
        try:
            wav = await transcode_to_wav(
                raw,
                self._temp_dir,
                sample_rate=sample_rate,
                channels=channels,
                timeout_seconds=self._timeout_seconds,
                ffmpeg=self._ffmpeg,
            )
        finally:
            raw.unlink(missing_ok=True)  # 原始上傳轉碼後即不再需要
        try:
            yield wav
        finally:
            wav.unlink(missing_ok=True)  # 輸出 wav 於 context 離開清除
