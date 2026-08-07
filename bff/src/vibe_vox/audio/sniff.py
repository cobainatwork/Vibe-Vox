"""音檔容器型別判定：僅依標頭 magic numbers，不信副檔名（設計 §2.1）。

sniffer 只做「容器層」保守判定，作為擋掉明顯非音訊檔的廉價前置閘；真正的解碼
驗證交給 ffmpeg。所有允許格式的 magic 皆落在檔首前 HEADER_BYTES 內，故不需大 buffer。
"""

# 判型所需的檔首位元組數。由本模組擁有而非各呼叫端各寫一次 12：這個數字是 magic 的
# 分布決定的，加一種容器就可能要改，而散在外面的那幾份不會跟著動。
HEADER_BYTES = 12


def detect_audio_format(header: bytes) -> str | None:
    """回傳允許容器型別名（wav/mp3/flac/ogg/m4a/webm）；不符或標頭過短回 None。"""
    if header[:4] == b"RIFF" and header[8:12] == b"WAVE":
        return "wav"
    if header[:3] == b"ID3":
        return "mp3"
    if len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xE0) == 0xE0:
        return "mp3"  # MPEG frame sync（無 ID3 標籤的裸幀）
    if header[:4] == b"fLaC":
        return "flac"
    if header[:4] == b"OggS":
        return "ogg"
    if header[4:8] == b"ftyp":
        return "m4a"  # MP4/M4A 家族：ftyp box 於 offset 4，不逐一列舉 brand
    if header[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"  # EBML（Matroska/WebM）
    return None
