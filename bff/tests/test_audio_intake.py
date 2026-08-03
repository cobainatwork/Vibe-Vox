"""#4 audio：串流落地 save_upload（不需 ffmpeg、處處可跑）。

seam：save_upload(chunks, *, temp_dir, max_bytes) — 串流寫 UUID 暫存檔，先累積
header window 判型別、超限即止。設計 §2.2、§5。
"""

import asyncio
import uuid

import pytest

from vibe_vox.audio.errors import FileTooLarge, UnsupportedAudioFormat
from vibe_vox.audio.intake import save_upload

_WAV_HEADER = b"RIFF\x00\x00\x00\x00WAVE"


async def _stream(chunks, consumed=None):
    for c in chunks:
        if consumed is not None:
            consumed.append(c)
        yield c


def test_saves_valid_audio_to_uuid_file(tmp_path):
    data = _WAV_HEADER + b"\x00" * 100
    path = asyncio.run(save_upload(_stream([data]), temp_dir=tmp_path, max_bytes=10_000))
    assert path.parent == tmp_path
    assert path.read_bytes() == data
    # 落地檔名為伺服器 UUID（32 hex，無副檔名、不含使用者輸入）
    assert len(path.name) == 32
    assert all(c in "0123456789abcdef" for c in path.name)


def test_small_file_below_header_window_still_saved(tmp_path):
    data = b"OggS" + b"\x00" * 20  # 24 bytes < 64 window：串流提前結束仍需判定並落地
    path = asyncio.run(save_upload(_stream([data]), temp_dir=tmp_path, max_bytes=10_000))
    assert path.read_bytes() == data


def test_rejects_forged_non_audio_before_writing(tmp_path):
    with pytest.raises(UnsupportedAudioFormat):
        asyncio.run(
            save_upload(_stream([b"MZ\x90\x00" + b"\x00" * 100]), temp_dir=tmp_path, max_bytes=10_000)
        )
    assert list(tmp_path.iterdir()) == []  # 拒絕於寫入前，未落地任何檔


def test_header_window_accumulated_across_tiny_chunks(tmp_path):
    # 首塊過短（4 bytes = "RIFF"，尚無法判 wav）不應誤拒；跨塊累積 window 後才判定。
    data = _WAV_HEADER + b"\x00" * 100
    chunks = [data[i : i + 4] for i in range(0, len(data), 4)]
    path = asyncio.run(save_upload(_stream(chunks), temp_dir=tmp_path, max_bytes=10_000))
    assert path.read_bytes() == data


def test_oversize_raises_and_stops_consuming_stream(tmp_path):
    consumed = []
    chunks = [_WAV_HEADER + b"\x00" * 60] + [b"\x00" * 50 for _ in range(100)]
    with pytest.raises(FileTooLarge):
        asyncio.run(
            save_upload(_stream(chunks, consumed), temp_dir=tmp_path, max_bytes=500)
        )
    assert len(consumed) < len(chunks)  # 超限即止，未耗盡輸入串流（佐證逐塊串流）
    assert list(tmp_path.iterdir()) == []  # 部分檔已清除


def test_o_excl_refuses_to_overwrite_existing_target(tmp_path, monkeypatch):
    fixed = uuid.UUID(int=1)
    monkeypatch.setattr("vibe_vox.audio.intake.uuid.uuid4", lambda: fixed)
    victim = tmp_path / fixed.hex
    victim.write_bytes(b"pre-existing")
    with pytest.raises(FileExistsError):
        asyncio.run(
            save_upload(_stream([_WAV_HEADER + b"\x00" * 100]), temp_dir=tmp_path, max_bytes=10_000)
        )
    assert victim.read_bytes() == b"pre-existing"  # O_EXCL：不覆寫既有目標
