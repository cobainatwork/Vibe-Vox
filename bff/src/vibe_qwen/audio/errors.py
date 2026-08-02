"""audio 模組例外（設計 §2.4）。

#4 僅定義並於模組測試斷言；HTTP 狀態碼映射待 #5／#7 接真實端點時於 main.py 註冊。
"""


class FileTooLarge(Exception):
    """上傳累計超過 max_bytes（端點層映射 → 413）。"""


class UnsupportedAudioFormat(Exception):
    """magic number 判定非允許音訊容器（端點層映射 → 400）。"""


class TranscodeError(Exception):
    """FFmpeg 轉碼失敗，含無法解碼的檔案（端點層映射 → 400）。"""


class TranscodeTimeout(Exception):
    """FFmpeg 子進程逾時被強制終止（端點層映射 → 504 語意）。"""
