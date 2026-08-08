"""TN：把書面形式的數字與符號改寫成台灣人會唸的口語形式（#46 Slice 1）。

`docs/api/tts.md` §7 對消費端承諾了這件事（`3kg` → 三公斤、`NT$1,250` → 新臺幣一千二百五十
元），而在此之前沒有實作——文字原樣進模型，唸法落到模型的 zh-CN 預設。**失效是靜默的**：
不回錯誤、不進 log，消費端拿到 200 與一段合法音訊，只是唸錯。

與 `tts_text` 分家而不是多加一個函式：那個模組守的是安全邊界（使用者文字不得成為控制
指令），本模組決定的是讀法。兩者改動的理由不同。

**不用 `wetext`（VoxCPM2 自己的正規化器）**，兩個理由都是實測的（#46 D1）：它的編譯相依
`kaldifst` 最新版只發到 cp312 wheel，而本專案的 image 是 3.13、開發機是 3.14，引入它等於把
BFF 執行期鎖在 3.12；而且它在我方在意的項目上多半是錯的（三千克、二十五攝氏度、第两名、
两千零二十六年），而 OpenCC 只修字形、修不了用詞與語序。

規則的組織方式：一條規則一個判準，彼此不共用狀態，順序在 `_RULES` 顯式宣告（#46 D3）。
不引入 FST 框架——這一層的價值在「可以逐條加台灣規則」，而框架會讓加一條規則變成改文法。
"""

import re
from collections.abc import Callable

_DIGITS = "零一二三四五六七八九"
# 位名。四位一組（萬、億）是中文的分節方式，故組內只到千。
_WITHIN_GROUP = ("", "十", "百", "千")
_GROUP_NAMES = ("", "萬", "億", "兆")


def _read_group(digits: str) -> str:
    """唸出四位以內的一組數字，處理組內的零。

    零不逐個唸出來而是「連續的零唸一個零、尾隨的零不唸」：1001 的組是 `1001` → 一千零一，
    1010 → 一千零一十。這是零的插入規則，也是這個函式存在的理由——若逐位加位名，會得到
    一千零百零十一。
    """
    out: list[str] = []
    size = len(digits)
    pending_zero = False
    for i, ch in enumerate(digits):
        value = int(ch)
        if value == 0:
            pending_zero = True
            continue
        if pending_zero and out:
            out.append(_DIGITS[0])
        pending_zero = False
        out.append(_DIGITS[value] + _WITHIN_GROUP[size - 1 - i])
    return "".join(out)


def _read_digits(raw: str) -> str:
    """逐位唸，不加位名。

    小數部分（三點一四 而非 三點十四——小數點後不是一個整數）、年號、電話、以及長到沒有
    位名可用的數字串都走這條。台灣的 1 唸「一」，不是大陸的「幺」。
    """
    return "".join(_DIGITS[int(ch)] for ch in raw)


def _read_integer(raw: str) -> str:
    """唸出一個非負整數。

    以四位一組切分（中文的分節在萬與億，不在千），逐組加組名。兩件事只在組之間成立、
    組內看不到，故必須在這一層做：

    - **全為零的組直接跳過**，不能補零：10000 是一萬而不是一萬零。
    - **本組不滿四位而高位組已唸過時要補一個零**：30500 是三萬零五百；100000001 是
      一億零一（中間整組為零，跳過之後仍由最低組補上那個零）。

    **超出位名範圍的長數字串逐位唸。** 位名只到「兆」（16 位），再長就沒有名字可加了；
    硬取會 IndexError 而冒成 500，而 500 不在契約 §6 的錯誤表內——一段使用者文字不該
    產出契約外的回應。逐位唸同時也是人對卡號、統編這類長數字串的實際唸法。
    """
    if len(raw) > 4 * len(_GROUP_NAMES):
        return _read_digits(raw)

    digits = raw.lstrip("0") or "0"
    if digits == "0":
        return _DIGITS[0]

    groups = []
    while digits:
        groups.append(digits[-4:])
        digits = digits[:-4]

    parts: list[str] = []
    for index in reversed(range(len(groups))):
        group = groups[index]
        spoken = _read_group(group)
        if not spoken:
            continue
        if parts and int(group) < 1000:
            parts.append(_DIGITS[0])
        parts.append(spoken + _GROUP_NAMES[index])

    reading = "".join(parts)
    # 十一 而非 一十一、十萬 而非 一十萬。只在整個數字的開頭成立——一百一十的那個
    # 一十 要留著，故這條是對最終字串的判斷而不是組內規則。
    return reading[1:] if reading.startswith("一十") else reading


def _read_number(raw: str) -> str:
    """唸出一個帶負號、千分位與小數點的數。"""
    sign = "負" if raw.startswith("-") else ""
    body = raw.lstrip("-").replace(",", "")
    integer, _, fraction = body.partition(".")
    # 前導零代表這是一組識別數字（電話、代碼、統編）而不是一個量：`0912` 不是九百一十二，
    # 而基數的唸法會把那個零整個吃掉。兩位以內不算（`08` 仍是八）。
    if len(integer) >= 3 and integer.startswith("0"):
        reading = sign + _read_digits(integer)
    else:
        reading = sign + _read_integer(integer or "0")
    return reading + "點" + _read_digits(fraction) if fraction else reading


# 半形化只做數字：全形標點是台灣書面的正常寫法，折成半形會讓輸出看起來像簡體排版，
# 且 tts_text 的中性化正是把控制字元轉成全形——在此折回去會拆掉那道邊界（#46 D1 記的
# WeTextProcessing `full_to_half` 陷阱，本模組不重蹈）。
_FULL_WIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")

# 一個數：可帶負號、千分位逗號與小數部分。負號只在數字前且前面不是數字時才算負號，
# 否則 `2026-08` 的連字號會被當成負數。
_NUMBER = re.compile(r"(?<![\d.])(-?)(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?")

# 溫度：台灣的語序是「攝氏二十五度」。zh-CN 框架產出「二十五攝氏度」——語序相反，
# 任何字形或用詞層的後處理都修不掉，故刻度名要在此就搬到數字前面。
_TEMPERATURE = re.compile(r"(-?\d+(?:\.\d+)?)\s*(℃|°C|℉|°F)")
_SCALES = {"℃": "攝氏", "°C": "攝氏", "℉": "華氏", "°F": "華氏"}

# 單位縮寫→台灣用詞。**多字母的單位比對不分大小寫、單字母的分**：`3M`（膠帶）與 `3G`
# （網路）在陪練對話裡出現的機會遠高於「三公尺」「三公克」寫成大寫，而 `15KG` 反過來。
_UNITS = {
    "kg": "公斤",
    "mg": "毫克",
    "cm": "公分",
    "mm": "公釐",
    "km": "公里",
    "ml": "毫升",
    "cc": "西西",
}
_SINGLE_LETTER_UNITS = {"m": "公尺", "g": "公克", "L": "公升", "l": "公升"}
# 長的單位要排在短的前面，否則 `500ml` 會先命中 `m` 而剩下一個裸的 l。
# `\s?` 讓 `3 kg` 這種帶空白的寫法也算——溫度與百分比都容空白，單位沒有理由不容。
_UNIT = re.compile(r"(?<=\d)\s?((?i:kg|mg|cm|mm|km|ml|cc)|m|g|[Ll])(?![A-Za-z])")


def _spell_phone(match: re.Match[str]) -> str:
    """逐位唸，段界放頓號。停頓是這條規則的重點——連著唸完的號碼記不下來。"""
    groups = [g for g in match.groups() if g]
    return "、".join(_read_digits(g) for g in groups)


def _spell_date(match: re.Match[str]) -> str:
    """`2026/8/5` → 二零二六年八月五日。年逐位、月與日是基數，前導零不唸。"""
    year, month, day = match.groups()
    return (
        f"{_read_digits(year)}年"
        f"{_read_integer(str(int(month)))}月"
        f"{_read_integer(str(int(day)))}日"
    )


def _spell_time(match: re.Match[str]) -> str:
    """`3:05` → 三點零五分。整點唸「整」，分鐘小於十要帶零否則聽起來像另一個數字。"""
    hour, minute = match.groups()
    spoken_hour = _read_integer(str(int(hour)))
    if int(minute) == 0:
        return f"{spoken_hour}點整"
    if int(minute) < 10:
        return f"{spoken_hour}點零{_DIGITS[int(minute)]}分"
    return f"{spoken_hour}點{_read_integer(minute)}分"


def _spell_twd(match: re.Match[str]) -> str:
    """`NT$1,250` → 新臺幣一千二百五十元；輸入已帶「元」則不再補。"""
    following = match.string[match.end() :].lstrip()
    suffix = "" if following[:1] in ("元", "塊") else "元"
    return f"新臺幣{match.group(1)}{suffix}"


def _spell_temperature(match: re.Match[str]) -> str:
    return f"{_SCALES[match.group(2)]}{match.group(1)}度"


def _spell_unit(match: re.Match[str]) -> str:
    unit = match.group(1)
    if len(unit) > 1:
        return _UNITS[unit.lower()]
    return _SINGLE_LETTER_UNITS[unit]


def _spell_plain_phone(match: re.Match[str]) -> str:
    """不分段的十位號碼（`0912345678`）：逐位唸並補上停頓。

    沒有這條的話它會落到通用數字規則，唸成「九億一千二百三十四萬…」而且前導零消失。
    """
    digits = match.group(0)
    return "、".join(
        _read_digits(part) for part in (digits[:4], digits[4:7], digits[7:])
    )


def _spell_short_date(match: re.Match[str]) -> str:
    """`10/20` → 十月二十日，但 `3/4` 留給分數規則。

    `M/D` 與分數在書面上同形，無法兩全。判準取「日大於 12」：那個數字不可能是月份，而
    分母大於 12 的分數在對話裡幾乎不出現。兩者都 ≤12 時（`3/4`）判為分數——「進度 3/4」
    比「三月四日」常見得多。契約 §7 記載了這條界線與繞道寫法。
    """
    month, day = int(match.group(1)), int(match.group(2))
    if 1 <= month <= 12 and 13 <= day <= 31:
        return f"{_read_integer(str(month))}月{_read_integer(str(day))}日"
    return match.group(0)


def _spell_percent(match: re.Match[str]) -> str:
    """`15%` → 百分之十五；`15-20%` → 百分之十五到二十。

    範圍由本規則一併吃掉而不是交給範圍規則：百分號在右運算元之後，範圍規則先跑會把兩個
    數字都換成中文而讓百分號失去依附，後跑則右邊已經是中文而比對不到。正確的唸法本來就是
    「百分之」分配到整個範圍。
    """
    low, high = match.groups()
    return f"百分之{low}到{high}" if high else f"百分之{low}"


# 全形標點在台灣書面是正常寫法，故**規則自己認兩種形式**，不把全文折成半形：後者會動到
# 規則不擁有的文字（`他說：` 的全形冒號），而 tts_text 的中性化正是把控制字元轉成全形，
# 在此折回去會拆掉那道邊界（#46 D1 記的 WeTextProcessing `full_to_half` 陷阱）。
_SLASH = "[/／]"
_DASH = "[-－]"
_COLON = "[:：]"
_PERCENT_SIGN = "[%％]"
_TILDE = "[~～]"
# 連字號要跳脫，否則它在字元類裡是範圍運算子而不是字面值。
_DATE_SEP_CHARS = r"/／\-－"
_DATE_SEP = f"[{_DATE_SEP_CHARS}]"

# 電話。逐位唸並在段界放頓號當停頓提示——連著唸完的號碼記不下來。首段要求 0 開頭，同時把
# `2026-08-05` 排除在外（它由日期規則處理）。
_PHONE_GROUPED = re.compile(
    rf"(?<![\d-])(0\d{{1,3}}){_DASH}(\d{{3,4}})(?:{_DASH}(\d{{3,4}}))?(?![\d-])"
)
_PHONE_PLAIN = re.compile(r"(?<!\d)0\d{9}(?!\d)")

# 完整日期（帶西元年）。**月與日要驗範圍**：不驗的話 `0912-345-678` 的 `0912` 也是四位數，
# 會被當成年份而把電話讀成日期。
_DATE = re.compile(
    rf"(?<![\d{_DATE_SEP_CHARS}])(\d{{4}}){_DATE_SEP}(\d{{1,2}}){_DATE_SEP}(\d{{1,2}})"
    rf"(?![\d{_DATE_SEP_CHARS}])"
)
# 沒有年份的 `M/D`。判準見 _spell_short_date；不符合的原樣留給分數規則。
_SHORT_DATE = re.compile(rf"(?<![\d./／])(\d{{1,2}}){_SLASH}(\d{{1,2}})(?![\d./／])")

# 時刻。分鐘限兩位且 ≤59，故 `1:1.5` 不會被當成時刻（那是比例）。
_TIME = re.compile(rf"(?<![\d:：])(\d{{1,2}}){_COLON}([0-5]\d)(?![\d:：])")

# 西元年：四位數接「年」，逐位唸。**民國年自然被排除**——它是二到三位數（民國 115 年），
# 而那個數字是「第幾年」而非一組年號數字，唸法本來就是基數，交給通用數字規則即可。
_AD_YEAR = re.compile(r"(?<!\d)(\d{4})(?=\s*年)")

# 台幣。只認 `NT$`：裸的 `$` 兩岸與美元都在用，猜幣別會把台幣唸成美元（實測 wetext
# 0.1.6 就是這樣錯的）。
#
# 「要不要補『元』」由替換函式看後文決定，**不能寫成 `(?!\s*[元塊])` 這種前瞻**：那會讓
# 引擎為了滿足前瞻而回溯去匹配較短的數字（`NT$1,250元` 因此比對到 `1,25`），輸出變成
# 「新臺幣一,二十五元零元」。前瞻是對匹配長度施壓，不是對是否套用規則施壓。
_TWD = re.compile(r"NT[$＄]\s*(-?[\d,]*\d(?:\.\d+)?)")

# 百分比（可含範圍，見 _spell_percent）、分數、比例、範圍。分數要把分母搬到前面
# （四分之三），故它是重排而非替換。
_NUM = r"[\d,]*\d(?:\.\d+)?"
_PERCENT = re.compile(
    rf"(?<!\d)(-?{_NUM})(?:\s*(?:{_DASH}|{_TILDE})\s*({_NUM}))?\s*{_PERCENT_SIGN}"
)
_FRACTION = re.compile(rf"(?<![\d./／])(\d+){_SLASH}(\d+)(?![\d./／])")
_RATIO = re.compile(rf"(?<![\d:：])(\d+(?:\.\d+)?){_COLON}(\d+(?:\.\d+)?)(?![\d:：])")
# 波浪號不限位數；**連字號的形式限三位以內**，否則 `2026-08`（年月）會被讀成範圍。
_RANGE_TILDE = re.compile(rf"(\d+(?:\.\d+)?)\s*{_TILDE}\s*(\d+(?:\.\d+)?)")
_RANGE_DASH = re.compile(
    rf"(?<![\d\-－])(\d{{1,3}}(?:\.\d+)?)\s*{_DASH}\s*(\d{{1,3}}(?:\.\d+)?)(?![\d\-－])"
)

# 二 → 兩。**這條的錯誤方向要對**：zh-CN 框架的問題是多唸了兩（第两名、两千零二十六年），
# 故預設是二，只在確定的位置改成兩。兩處：
#
# 1. 百／千／萬／億 的首位（兩百、兩萬）。**前面不能有別的數字或位名**——一千二百五十的
#    那個二在數字中段，契約 §7 逐字寫的就是「一千二百五十」。
# 2. 常見量詞之前（兩個、兩公斤、兩點）。
#
# 量詞是一份**刻意不完整**的清單：漏掉的唸成二仍然聽得懂，而誤收的（月、號、樓、日）會唸
# 成明確錯誤的兩月、兩號。清單可逐條加，這正是規則式的好處。
#
# **「點」要算進前置排除**：小數點後的位不是量詞前的二，`1.2kg` 是一點二公斤而不是
# 一點兩公斤——而那是報價單上最常見的寫法之一。序數的「第」同理，且兩條都要排除它
# （`第2000大` 是第二千大）。
_NUMERALS = "零一二三四五六七八九十百千萬億點"
_TWO_BEFORE_GROUP = re.compile(rf"(?<![{_NUMERALS}第])二(?=[百千萬億])")

# 多字的量詞由單位表導出而非再列一次：新增一個單位時它就自動拿到「兩」的處理，不會出現
# 「兩公斤」對而「二毫升」漏掉的狀態。
_MULTI_CHAR_MEASURES = (
    *sorted(set(_UNITS.values()) | set(_SINGLE_LETTER_UNITS.values())),
    "小時",
    "分鐘",
)
_TWO_BEFORE_MEASURE = re.compile(
    rf"(?<![{_NUMERALS}第])二"
    rf"(?=(?:{'|'.join(_MULTI_CHAR_MEASURES)}|[個位次天週年杯瓶張本台件種倍隻元塊點]))"
)

# 夾在漢字之間的空白。規則替換會留下這種空白（wetext 就是這樣產出「第 兩名」「新臺幣 三
# 萬元」的），而空白不發音，拿掉沒有損失。**只拿掉兩側都是漢字的**：拉丁文字兩側的空白是
# 詞界。
_SPACE_BETWEEN_HAN = re.compile(r"(?<=[一-鿿])[ \t]+(?=[一-鿿])")

# 落單的波浪號。範圍規則跑之前，時刻規則可能已經把兩端換成中文（`10:00~11:30`），使範圍
# 比對不到而留下一個不發音的符號。這是收尾，不是範圍規則的替代。
#
# **判準是「左邊是數字或時間量詞」，不是「兩邊都是漢字」。** 後者曾經成立過，因為這條只被
# 時刻範圍的案例驗過——而口語的拉長音同樣夾在漢字之間，於是「你好～我是」被唸成「你好到
# 我是」，整句就毀了。真實逐字稿裡每四句就出現一次（tests/fixtures/real_dialogues.json）。
#
# 左邊收 `整分秒日號月年時` 是因為時刻與日期規則的產物以量詞結尾（`十點整`、`三點零五分`、
# `二零二六年八月五日`），右邊只需要數字——範圍的另一端一定是數字開頭。
_TILDE_LEFT = _NUMERALS + "整分秒日號月年時"
_TILDE_BETWEEN_NUMBERS = re.compile(rf"(?<=[{_TILDE_LEFT}]){_TILDE}(?=[{_NUMERALS}])")

# 規則與其順序。**順序有實質後果**，故它是一份看得到的清單而不是散在函式裡的呼叫：
#
# - 半形化最先，否則後面每條規則都要認兩套字元。
# - 需要「重新排列」或「換個講法」的規則（溫度、單位）在朗讀數字之前跑，它們只改寫符號
#   與位置、把數字留在原地；數字的朗讀最後一次做完。反過來的話這些規則就得比對中文數字。
_Replacement = str | Callable[[re.Match[str]], str]
_RULES: tuple[tuple[re.Pattern[str], _Replacement], ...] = (
    # 這幾條各自產出最終讀音（逐位、或重排成年月日），因為它們要的不是基數。跑在通用數字
    # 規則之前，而且彼此的順序也有意義：
    #
    # - 電話先於日期：兩者都吃連字號。
    # - 日期先於分數（否則 `2026/8/5` 變成分數）、時刻先於比例（否則 `3:05` 變成三比零五）。
    # - **台幣先於西元年**：`NT$8800 年繳` 若讓年份規則先跑，數字被換成中文後 `NT$` 失去
    #   可比對的數字而原樣留下，模型會把它當字母唸。
    (_PHONE_GROUPED, _spell_phone),
    (_PHONE_PLAIN, _spell_plain_phone),
    (_DATE, _spell_date),
    (_SHORT_DATE, _spell_short_date),
    (_TIME, _spell_time),
    (_TWD, _spell_twd),
    (_AD_YEAR, lambda m: _read_digits(m.group(1))),
    (_PERCENT, _spell_percent),
    (_FRACTION, r"\2分之\1"),
    (_RATIO, r"\1比\2"),
    (_RANGE_TILDE, r"\1到\2"),
    (_RANGE_DASH, r"\1到\2"),
    (_TEMPERATURE, _spell_temperature),
    (_UNIT, _spell_unit),
    (_NUMBER, lambda m: _read_number(m.group(0))),
    # 兩的兩條在數字朗讀之後：它們判斷的是中文數字的前後文，而那要等數字唸出來才存在。
    (_TWO_BEFORE_GROUP, "兩"),
    (_TWO_BEFORE_MEASURE, "兩"),
    (_TILDE_BETWEEN_NUMBERS, "到"),
    # 收空白必須最後：前面每一條都可能製造出夾在漢字之間的空白。
    (_SPACE_BETWEEN_HAN, ""),
)


def to_spoken_form(text: str) -> str:
    """把書面形式改寫成口語形式。純函式、無 I/O、不載入模型。"""
    text = text.translate(_FULL_WIDTH_DIGITS)
    for pattern, replace in _RULES:
        text = pattern.sub(replace, text)
    return text
