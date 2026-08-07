"""合成前的文字前處理層，位於 TtsClient 之上（#15 findings 的管線位置）。

目前只做控制語法的中性化。完整管線（wetext TN、OpenCC s2tw、g2pW 破音字）尚未實作，
沒有它台灣讀音與數字唸法會落到 zh-CN 的行為——該工作沒有票，見交接文件。

**中性化是安全邊界不是潔癖。** 送給 VoxCPM2 的文字裡，半形括號是風格指令的語法、
大括號是讀音標記的語法，兩者都由 BFF 組裝。使用者文字若原樣通過，一句「他說(笑)」
就會變成語氣指令；instruct 裡的右括號更能跳出前綴注入任意內容。
"""

import re
import unicodedata

# 轉全形而非刪除：括號內容是使用者想唸出來的東西，保留它；失去的只有控制語意。
# 契約 §5.1 已告知消費端「要唸出括號請用全形」，故全形是既定的安全寫法。
#
# **實作 TN 層時這個策略要重新檢查。** WeTextProcessing 的 `full_to_half` 預設為 True，
# 而 #15 定的管線順序是「淨化 {} → TN」——TN 一旦接上，它會把這裡轉出來的全形括號折回
# 半形，整道邊界失效。屆時要嘛關掉 full_to_half，要嘛改成刪除而非轉全形。
#
# 同理，Unicode 相容等價的括號變體（U+FE59 ﹙、U+207D ⁽、U+208D ₍、U+FE35 ︵ 等，NFKC
# 都折回半形）目前刻意不處理：上游不做正規化（`normalize=False`，端點側只 tokenize），
# 所以它們到不了控制語法的位置。TN 接上後就會，那是同一件事的另一面。
_NEUTRALIZED = str.maketrans({"(": "（", ")": "）", "{": "｛", "}": "｝"})

# <|...|> 特殊 token 標記，整段移除。理由與作法沿用 ASR 側的 hotword_text.sanitize_text：
# 上游是 `tokenizer.encode(text, add_special_tokens=True)` 直吃我們送的字串，而 HF 的
# fast tokenizer 會把文字中的 added special token 比對成 token id，使模型看到的不是
# 字面內容而是控制訊號。TTS 走的是同一類通道，沒有理由只在 ASR 側防。
_SPECIAL_TOKEN = re.compile(r"<\|.*?\|>")


def neutralize_control_syntax(text: str) -> str:
    """移除特殊 token 標記，並把控制語法的保留字元轉為全形等價物。

    **移除跑到不動點而非只跑一次。** `<\\|.*?\\|>` 是非貪婪匹配，移除一層之後殘骸可能
    重新組成一個合法的標記：`<<||>|x|>` 的中間段 `<||>` 被吃掉後剩下 `<|x|>`，那仍是
    特殊 token 標記。單次替換會讓它原樣送進 tokenizer。

    收斂必然終止：每一輪都嚴格縮短字串，否則迴圈就結束了。全形轉換不需要迴圈——它是
    冪等的（全形字元不在對照表裡）。
    """
    while (stripped := _SPECIAL_TOKEN.sub("", text)) != text:
        text = stripped
    return text.translate(_NEUTRALIZED)


# 不發音的 Unicode 大類：標點（P）、分隔（Z）、符號（S，含 emoji）、控制字元（C）。
# 保留字母（L）、數字（N）與標記（M，如注音符號的聲調），那些是唸得出來的東西。
_SILENT_CATEGORIES = ("P", "Z", "S", "C")


def _has_speakable_content(text: str) -> bool:
    """判斷文字裡是否有唸得出來的東西。

    契約 §7 把「只有標點或空白」與「只有 emoji」都算成空輸入：送出去只會拿到一段沒有
    內容的音訊，還佔一次 GPU。以 Unicode 大類判斷而非列舉標點表——後者永遠列不完，
    而全形、半形與各語系的標點都得算進去。

    私有：它**只在中性化之後**才有意義，見 `to_speakable`。
    """
    return any(
        unicodedata.category(ch)[0] not in _SILENT_CATEGORIES for ch in text
    )


def to_speakable(raw: str) -> str | None:
    """把使用者輸入化為可送去合成的文字；沒有可唸的內容時回 `None`。

    **中性化與判空是同一個不可分割的步驟**，這是本函式存在的唯一理由。兩者分屬兩處
    時，判空必然量在中性化之前——而 `<|im_end|>` 這種整段會被移除的輸入，靠 `i`／`m`
    ／`e`／`n`／`d` 這幾個字母就能通過判空，接著佔一個 heavy guard 額度、打一次 GPU、
    回一段空音訊 200。契約 §6 要的是 400 `EMPTY_INPUT`（「input 為空，或經正規化後為
    空」）。

    長度上限也必須量在回傳值上而非原始輸入上，理由同上：被移除的字元不該算進額度。
    故 strip 在中性化**之後**——標記兩側的空白在移除標記後才浮出來，先 strip 會讓它們
    留在字串裡繼續吃額度。

    回 `None` 而不自己拋例外：端點層已經有 `EmptyInput` 與其 handler，本層再定義一個
    等價的例外型別只是多一次轉換。（同層的 `hotword_text.clean_term` 確實拋例外，那是
    因為它沒有對應的端點層型別可用。）
    """
    text = neutralize_control_syntax(raw).strip()
    return text if _has_speakable_content(text) else None
