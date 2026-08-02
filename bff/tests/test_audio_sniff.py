"""#4 audio：magic number 型別判定（純函式，零依賴、處處可跑）。

seam：detect_audio_format(header) — 僅依標頭位元組判容器型別，不信副檔名。
"""

from vibe_qwen.audio.sniff import detect_audio_format


def test_detects_each_allowed_container_by_magic():
    assert detect_audio_format(b"RIFF\x00\x00\x00\x00WAVE\x00\x00\x00") == "wav"
    assert detect_audio_format(b"ID3\x04\x00\x00\x00\x00\x00\x00") == "mp3"
    assert detect_audio_format(b"\xff\xfb\x90\x00\x00\x00\x00\x00") == "mp3"  # frame sync
    assert detect_audio_format(b"fLaC\x00\x00\x00\x22\x00\x00\x00") == "flac"
    assert detect_audio_format(b"OggS\x00\x02\x00\x00\x00\x00\x00") == "ogg"
    assert detect_audio_format(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00") == "m4a"
    assert detect_audio_format(b"\x1a\x45\xdf\xa3\x01\x00\x00\x00") == "webm"


def test_rejects_non_audio_and_forged_headers():
    assert detect_audio_format(b"MZ\x90\x00\x03\x00\x00\x00") is None  # PE/exe
    assert detect_audio_format(b"%PDF-1.7\x00\x00\x00\x00") is None
    assert detect_audio_format(b"not audio at all") is None
    assert detect_audio_format(b"RIFF\x00\x00\x00\x00AVI ") is None  # RIFF 但非 WAVE
    assert detect_audio_format(b"") is None


def test_large_id3_tag_still_detected_as_mp3():
    # ID3v2 的 mp3 開頭即 `ID3` magic；大型封面圖標籤不影響檔首判定。
    header = b"ID3\x04\x00\x00" + b"\x7f" * 6 + b"\x00" * 5000
    assert detect_audio_format(header) == "mp3"


def test_too_short_header_is_none_not_error():
    # 首塊過短時不應誤判或拋錯（呼叫端會累積 header window 後再判）。
    assert detect_audio_format(b"RI") is None
    assert detect_audio_format(b"\x00\x00\x00\x18ft") is None  # ftyp 尚不完整
