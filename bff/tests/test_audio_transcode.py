"""#4 audio：FFmpeg 轉碼包裝 transcode_to_wav（需 ffmpeg，本機無則 skip、CI 跑）。

seam：transcode_to_wav(src, dst_dir, *, sample_rate, channels, timeout_s, ffmpeg)。
取樣率以解析輸出 WAV 標頭驗證（零依賴、不需 ffprobe）。設計 §2.3、§5。
"""

import asyncio
import os
import shutil
import struct
import subprocess

import pytest

from vibe_qwen.audio.errors import TranscodeError, TranscodeTimeout
from vibe_qwen.audio.intake import AudioIntake
from vibe_qwen.audio.transcode import transcode_to_wav

need_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")


def _make_sine(path, *, seconds=1, rate=44100):
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={seconds}", "-ar", str(rate),
         "-y", str(path)],
        check=True,
    )


def _wav_sample_rate(path):
    b = path.read_bytes()
    assert b[:4] == b"RIFF" and b[8:12] == b"WAVE"
    i = 12
    while i + 8 <= len(b):
        chunk_id = b[i : i + 4]
        size = struct.unpack_from("<I", b, i + 4)[0]
        if chunk_id == b"fmt ":
            # fmt data：audioFormat(2) numChannels(2) sampleRate(4) ...
            return struct.unpack_from("<I", b, i + 8 + 4)[0]
        i += 8 + size + (size & 1)
    raise AssertionError("WAV 無 fmt chunk")


@need_ffmpeg
def test_transcode_resamples_to_target_sample_rate(tmp_path):
    src = tmp_path / "in.wav"
    _make_sine(src, rate=44100)
    dst = asyncio.run(transcode_to_wav(src, tmp_path, sample_rate=16000, channels=1, timeout_seconds=30))
    assert dst.suffix == ".wav" and dst.parent == tmp_path
    assert _wav_sample_rate(dst) == 16000


@need_ffmpeg
def test_undecodable_input_raises_transcode_error(tmp_path):
    src = tmp_path / "junk.bin"
    src.write_bytes(b"\x00" * 4096)  # 非音訊，ffmpeg 無法解碼
    with pytest.raises(TranscodeError):
        asyncio.run(transcode_to_wav(src, tmp_path, sample_rate=16000, timeout_seconds=30))
    # 失敗不留殘檔
    assert not any(p.suffix == ".wav" for p in tmp_path.iterdir())


@need_ffmpeg
@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="需 POSIX FIFO 製造確定性掛起")
def test_timeout_kills_hung_subprocess(tmp_path):
    fifo = tmp_path / "hang.wav"
    os.mkfifo(fifo)  # 無寫入端 → ffmpeg 讀取時無限掛起
    with pytest.raises(TranscodeTimeout):
        asyncio.run(transcode_to_wav(fifo, tmp_path, sample_rate=16000, timeout_seconds=0.5))
    # transcode_to_wav 於逾時時 proc.kill() + await proc.wait()，故無孤兒程序、無殘檔
    assert not any(p.suffix == ".wav" for p in tmp_path.iterdir())


async def _stream(chunks):
    for c in chunks:
        yield c


@need_ffmpeg
def test_transcoded_facade_yields_wav_and_cleans_up(tmp_path):
    # facade（設計 §7 共用進入點）：串流上傳 → 轉碼 → yield wav → 離開即清兩暫存檔。
    src = tmp_path / "src.wav"
    _make_sine(src, rate=44100)
    data = src.read_bytes()
    work = tmp_path / "work"
    intake = AudioIntake(temp_dir=work, max_bytes=10_000_000, timeout_seconds=30)

    async def scenario():
        async with intake.transcoded(_stream([data]), sample_rate=16000) as wav:
            assert wav.exists()
            assert _wav_sample_rate(wav) == 16000
            return wav

    wav = asyncio.run(scenario())
    assert not wav.exists()  # 輸出 wav 於 context 離開刪除
    assert list(work.iterdir()) == []  # 原始暫存與輸出皆清除（用畢即清）
