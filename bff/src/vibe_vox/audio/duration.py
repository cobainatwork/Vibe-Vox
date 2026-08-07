"""音檔時長量測：wav 走 stdlib、其餘容器起 ffprobe。

**量不到就拋，不回退預設值。** 呼叫端有兩種需求：ASR 那條路寧可讓辨識繼續（見
`adapters/vllm_asr._audio_duration` 的 1.0 回退），參考音的驗證則必須把「量不到」
當成驗證失敗——沿用回退等於把 0 秒與 40 秒的檔案都當成合格的 1 秒（#44）。兩種政策
分屬呼叫端，本模組只負責量得到或量不到。

分兩條路不是最佳化：ffprobe 不在 PATH 的環境（開發機、精簡 image）在 wav 上仍量得到，
而 wav 是本專案自己產出的容器（`transcode_to_wav` 的輸出、ASR 的正規化結果）。
"""

import asyncio
import wave
from pathlib import Path

from vibe_vox.audio.sniff import HEADER_BYTES, detect_audio_format

# ffprobe 只讀檔頭的 metadata，正常在一秒內完成；30 秒是留給慢速磁碟與大檔的餘裕。
# 刻意遠小於 ffmpeg_timeout（60）與 asr_timeout（300）：這一步掛住不該吃掉整個請求的
# 預算。不進 config：它不是部署會調的值。
_FFPROBE_TIMEOUT_SECONDS = 30.0


class DurationUnavailable(Exception):
    """量不到時長：檔案不存在或不可讀、非可解碼音訊、ffprobe 不在 PATH 或逾時。"""


async def probe_duration(path: Path) -> float:
    """回音檔秒數。量不到即 `DurationUnavailable`。

    容器型別從檔頭嗅而非看副檔名：參考音原樣落地且檔名是無副檔名的 UUID
    （`api/admin_voices.py`）。嗅不出來的一律交給 ffprobe——它認得的格式比本專案
    允許的六種多，而真的讀不出來時它也會失敗。
    """
    try:
        with path.open("rb") as fh:
            header = fh.read(HEADER_BYTES)
    except OSError as exc:
        raise DurationUnavailable(path) from exc

    if detect_audio_format(header) == "wav":
        return _wav_duration(path)
    return await _ffprobe_duration(path)


def _wav_duration(path: Path) -> float:
    """以 stdlib 讀 wav 標頭算秒數，不起子進程。

    只讀標頭故不受檔案大小影響；framerate 為 0 的畸形標頭要當成量不到而非除以零。
    """
    try:
        with wave.open(str(path), "rb") as w:
            rate = w.getframerate()
            frames = w.getnframes()
    except (OSError, wave.Error, EOFError) as exc:
        raise DurationUnavailable(path) from exc
    if rate <= 0:
        raise DurationUnavailable(path)
    return frames / rate


async def _ffprobe_duration(path: Path) -> float:
    """以 ffprobe 取秒數（對齊官方 test_api.py），支援 wav 以外的各容器。

    以 asyncio 子進程而非 `subprocess.check_output`：後者同步阻塞 event loop，會使
    guard 的 `asyncio.timeout` 無法觸發——ffprobe 掛住時整個請求只剩反向代理能收尾，
    使用者拿到 HTML 錯誤頁，而那正是 #35 要消除的結果。
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError as exc:
        raise DurationUnavailable(path) from exc  # ffprobe 不在 PATH
    try:
        async with asyncio.timeout(_FFPROBE_TIMEOUT_SECONDS):
            out, _ = await proc.communicate()
    except (TimeoutError, asyncio.CancelledError) as exc:
        proc.kill()
        await proc.wait()
        if isinstance(exc, asyncio.CancelledError):
            raise  # 連線中斷要繼續向上傳播，與 transcode 一致
        raise DurationUnavailable(path) from exc
    if proc.returncode != 0:
        raise DurationUnavailable(path)
    try:
        # 無 duration metadata 的來源會輸出 "N/A"，那也是量不到。
        return float(out.decode().strip())
    except ValueError as exc:
        raise DurationUnavailable(path) from exc
