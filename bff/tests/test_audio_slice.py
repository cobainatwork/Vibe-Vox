"""逐段切片 slice_wav：seam 為純函式，直接呼叫。

輸入恆為 AudioIntake.transcoded() 產出的 pcm_s16le 單聲道 wav，故不需 ffmpeg，
以 stdlib wave 做 byte 層切片、全程不落檔。測試音檔的每個 frame 值等於其索引，
故預期值由建構方式決定而非由實作重算。
"""

import io
import wave
from pathlib import Path

from vibe_vox.audio.slice import slice_wav, wav_duration

_RATE = 1000  # 真實輸入為 24000；取樣率不影響切片邏輯，小值使斷言的算術一目了然


def _write_wav(
    path: Path, *, frames: int, rate: int = _RATE, channels: int = 1, width: int = 2
) -> Path:
    samples = b"".join(
        (i % 1000).to_bytes(width, "little") * channels for i in range(frames)
    )
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(samples)
    return path


def _read_frames(wav: bytes) -> list[int]:
    with wave.open(io.BytesIO(wav), "rb") as w:
        raw = w.readframes(w.getnframes())
    return [int.from_bytes(raw[i : i + 2], "little") for i in range(0, len(raw), 2)]


def test_wav_duration_reads_actual_length(tmp_path):
    # alignment.audio_duration 要的是音檔實際總長，而 TranscriptionResult.duration
    # 是 Segment End 最大值——尾端靜音不計入，恆小於或等於實際長度
    # （docs/api/asr.md §4.2）。故不可用後者代替。
    src = _write_wav(tmp_path / "a.wav", frames=int(2.5 * _RATE))

    assert wav_duration(src) == 2.5


def test_slice_extracts_requested_interval(tmp_path):
    # 3 秒音檔取 [1.0, 2.0) → 第 1000 至 1999 個 frame，其值為 0..999。
    src = _write_wav(tmp_path / "a.wav", frames=3 * _RATE)

    result = slice_wav(src, start=1.0, end=2.0)

    assert result.start == 1.0
    assert result.frames == 1000
    assert _read_frames(result.wav) == list(range(1000))


def test_slice_clamps_negative_start_to_zero(tmp_path):
    # 第一段的 Start 為 0，減 buffer 後為負。夾限至 0 而非拋錯，且回報的 start
    # 須為實際的 0——呼叫端拿它當 offset，若回報 -0.5 則整段時間戳左移。
    src = _write_wav(tmp_path / "a.wav", frames=3 * _RATE)

    result = slice_wav(src, start=-0.5, end=1.0)

    assert result.start == 0.0
    assert _read_frames(result.wav) == list(range(1000))


def test_slice_stops_at_end_of_audio(tmp_path):
    # 末段的 End 加 buffer 後超出音檔長度。切到檔尾為止，不拋錯也不補靜音——
    # 補靜音會讓對齊多出一段無對應文字的音訊。
    src = _write_wav(tmp_path / "a.wav", frames=3 * _RATE)

    result = slice_wav(src, start=2.5, end=3.5)

    assert result.start == 2.5
    assert _read_frames(result.wav) == list(range(500, 1000))


def test_slice_returns_empty_when_start_beyond_audio(tmp_path):
    # 模型時間戳幻覺可使段落起點落在音檔外。wave.setpos 對超界位置直接拋
    # wave.Error，會讓低階例外冒成 500；夾限至檔尾則得空切片，交由呼叫端的既有
    # 降級路徑處理。
    src = _write_wav(tmp_path / "a.wav", frames=1 * _RATE)

    result = slice_wav(src, start=5.0, end=6.0)

    assert result.start == 1.0  # 夾限至音檔長度
    assert result.frames == 0  # 呼叫端據此剔除退化段落；wav 仍有 header 故不可看長度
    assert _read_frames(result.wav) == []


def test_slice_end_reports_what_was_actually_read(tmp_path):
    # end 自實際讀到的 frame 數導出而非由請求的 end 夾限：末段的請求區間超出檔尾時
    # 兩者不同，而落界判準要的是真正有音訊的範圍。
    src = _write_wav(tmp_path / "a.wav", frames=3 * _RATE)

    within = slice_wav(src, start=1.0, end=2.0)
    past_end = slice_wav(src, start=2.5, end=3.5)

    assert (within.start, within.end) == (1.0, 2.0)
    assert (past_end.start, past_end.end) == (2.5, 3.0)  # 請求到 3.5，檔只到 3.0


def test_bounds_widen_to_millisecond_grid(tmp_path):
    # start／end 落在 frame 格點上，而對齊結果的時間戳取三位小數，兩者的格點不同：
    # 恰好對到切片邊界的字在四捨五入後會跑到範圍外，使正常段落被落界判準攔下。向外
    # 取整讓範圍涵蓋所有能捨入到它的時間戳。
    #
    # 24000 Hz 下第 24001 個 frame 落在 1.00004166… 秒，是這個錯位的最小可重現形式。
    src = _write_wav(tmp_path / "a.wav", frames=3 * 24000, rate=24000)

    result = slice_wav(src, start=1.0000417, end=2.0000417)

    assert result.start > 1.0 and result.end > 2.0  # 實際邊界不在毫秒格點上
    lower, upper = result.bounds
    assert (lower, upper) == (1.0, 2.001)  # 下界向下、上界向上
    assert lower <= round(result.start, 3) and round(result.end, 3) <= upper


def test_slice_preserves_audio_parameters(tmp_path):
    # aligner 端以 libsndfile 讀 header 取取樣率、且不重取樣（align 內部才轉 16k）。
    # header 若寫死或漏帶，秒數換算即錯，時間戳整段失真而不報錯。
    src = _write_wav(tmp_path / "a.wav", frames=2 * 24000, rate=24000)

    result = slice_wav(src, start=0.5, end=1.0)

    with wave.open(io.BytesIO(result.wav), "rb") as w:
        assert w.getframerate() == 24000
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getnframes() == 12000  # 0.5 秒 @ 24 kHz
