"""FFmpeg 子進程轉碼包裝（設計 §2.3）。

以 asyncio 子進程呼叫 ffmpeg，參數一律 list、絕不經 shell、絕不字串拼接；加
-protocol_whitelist file 阻斷 concat/http 等協定以防 SSRF 與本機檔案讀取。逾時以
asyncio.timeout 中止並強制 kill 子進程（Linux SIGKILL、Windows TerminateProcess），
避免掛起解碼器殘留佔用資源。
"""

import asyncio
import uuid
from pathlib import Path

from vibe_vox.audio.errors import TranscodeError, TranscodeTimeout


async def transcode_to_wav(
    src: Path,
    dst_dir: Path,
    *,
    sample_rate: int,
    channels: int = 1,
    timeout_seconds: float,
    ffmpeg: str = "ffmpeg",
) -> Path:
    """將 src 轉為 wav（pcm_s16le / 指定取樣率 / 指定聲道），回傳輸出路徑。

    無法解碼 → TranscodeError；逾時 → TranscodeTimeout（子進程強制終止）。
    src/dst 皆為伺服器控制的路徑，使用者字串永不進參數或路徑。
    """
    src = Path(src)
    dst_dir = Path(dst_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f"{uuid.uuid4().hex}.wav"

    args = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel", "error",
        "-protocol_whitelist", "file",
        "-i", str(src),
        "-ar", str(sample_rate),
        "-ac", str(channels),
        "-f", "wav",
        "-acodec", "pcm_s16le",
        "-y", str(dst),
    ]

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(timeout_seconds):
            _, stderr = await proc.communicate()
    except (TimeoutError, asyncio.CancelledError) as exc:
        # 逾時或取消（如 HTTP 連線中斷）：強制殺子進程、回收、清檔。
        proc.kill()
        await proc.wait()
        dst.unlink(missing_ok=True)
        if isinstance(exc, asyncio.CancelledError):
            raise  # 讓取消繼續向上傳播
        raise TranscodeTimeout()

    if proc.returncode != 0:
        dst.unlink(missing_ok=True)
        detail = stderr.decode("utf-8", "replace").strip() if stderr else ""
        raise TranscodeError(detail)
    return dst
