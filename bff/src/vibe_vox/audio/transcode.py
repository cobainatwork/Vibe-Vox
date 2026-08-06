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


async def _run_ffmpeg(
    args: list[str], *, stdin_data: bytes | None, timeout_seconds: float
) -> bytes:
    """跑 ffmpeg 並回 stdout。

    這段是本模組唯一不能漂移的東西，故只有一份：逾時要強制 kill 子進程（掛起的解碼器
    會一直佔著資源）、取消要繼續向上傳播（HTTP 連線中斷時呼叫端要收得到）、非零
    returncode 要帶 stderr 內容拋出。兩個呼叫端只差參數表與來源／去處。
    """
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE if stdin_data is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(timeout_seconds):
            stdout, stderr = await proc.communicate(stdin_data)
    except (TimeoutError, asyncio.CancelledError) as exc:
        proc.kill()
        await proc.wait()
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise TranscodeTimeout() from exc

    if proc.returncode != 0:
        raise TranscodeError(stderr.decode("utf-8", "replace").strip() if stderr else "")
    return stdout


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

    try:
        await _run_ffmpeg(args, stdin_data=None, timeout_seconds=timeout_seconds)
    except BaseException:
        # 逾時、轉碼失敗、或呼叫端取消（HTTP 連線中斷）都不留半成品。
        dst.unlink(missing_ok=True)
        raise
    return dst


async def resample_wav_to_pcm(
    data: bytes,
    *,
    sample_rate: int,
    channels: int = 1,
    timeout_seconds: float,
    ffmpeg: str = "ffmpeg",
) -> bytes:
    """把 wav 位元組重取樣為裸 PCM（s16le），全程走管線不落磁碟。

    與 transcode_to_wav 的差別在來源與去處都是記憶體：合成回應是即時產生的、只活到
    回應送出為止，寫進磁碟再讀回來只是多一次 I/O 與一份要清的暫存檔。

    輸入格式以 -f wav 明確指定而非讓 ffmpeg 猜測，避免非預期的 demuxer 被觸發；
    protocol whitelist 只留 pipe，容器內的檔案系統與網路一律不可達。
    """
    args = [
        ffmpeg,
        "-hide_banner",
        "-loglevel", "error",
        "-protocol_whitelist", "pipe",
        "-f", "wav",
        "-i", "pipe:0",
        "-ar", str(sample_rate),
        "-ac", str(channels),
        "-f", "s16le",
        "-acodec", "pcm_s16le",
        "pipe:1",
    ]

    return await _run_ffmpeg(args, stdin_data=data, timeout_seconds=timeout_seconds)
