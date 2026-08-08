"""中文字形轉換（OpenCC）。兩個方向，各服務一端模型的字形要求。

**兩者都不是產品語義，是模型端的輸入／輸出格式。** VibeVoice-ASR 語料以簡體為主、中文
轉錄輸出簡體字形，故回給使用者之前轉繁；VoxCPM2 對特定繁體字會落到**粵語**發音（#51
實測：同一句同一音色，繁體 7/8 錯、簡體 0/8），故送進模型之前轉簡。放在同一個模組是
因為它們是同一件事的兩半——換掉任一端的模型，對應的那一半就跟著不需要了。

繁→簡用 t2s、簡→繁用 s2tw（非 s2twp）：只做字形轉換、不做慣用詞替換（「視頻」不會被
改成「影片」），以保留辨識出的實際詞彙。OpenCC 載入字典成本高，兩個方向各以 lru_cache
做單例、延遲初始化。

**`tts_g2p` 另有一個 t2s 單例，那不是疏漏。** 它轉出來的字串不送給任何人，只用來查
`pypinyin` 的詞典（那份詞典 47,111 條全是簡體）。前處理層不依賴 adapters，共用得把這裡
搬成中立模組——多載一次字典的成本換那次跨層重構不划算。
"""

from functools import lru_cache

from opencc import OpenCC


@lru_cache(maxsize=1)
def _s2tw() -> OpenCC:
    return OpenCC("s2tw")


@lru_cache(maxsize=1)
def _t2s() -> OpenCC:
    return OpenCC("t2s")


def to_traditional(text: str) -> str:
    if not text:
        return text
    return _s2tw().convert(text)


def to_simplified(text: str) -> str:
    if not text:
        return text
    return _t2s().convert(text)
