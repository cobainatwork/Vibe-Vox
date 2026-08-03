"""簡體→台灣繁體字形轉換（OpenCC s2tw）。

VibeVoice-ASR 語料以簡體為主、中文轉錄輸出簡體字形。此處只做字形轉換，
用 s2tw（非 s2twp）以保留辨識出的實際詞彙、不做慣用詞替換（如「視頻」不會被
改成「影片」）。OpenCC 載入字典成本高，以 lru_cache 做單例、延遲初始化。
"""

from functools import lru_cache

from opencc import OpenCC


@lru_cache(maxsize=1)
def _converter() -> OpenCC:
    return OpenCC("s2tw")


def to_traditional(text: str) -> str:
    if not text:
        return text
    return _converter().convert(text)
