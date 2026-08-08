"""把讀音與模型預設不同的字詞，就地換成 VoxCPM2 的 `{pinyin+聲調}` 讀音標記（#46、#50）。

模型唸錯分三類，本模組各有一個機制，**三者都只在原文上算位置，最後一次注入**：

| 成因 | 例 | 機制 |
|---|---|---|
| A. 上下文改變讀音 | 銀行 háng、給付 jǐ、出差 chāi | `_context_readings`（#50） |
| B1. 兩岸讀音不同 | 垃圾 lè sè、品質 zhí、期限 qí | 下面兩張表（#46） |
| B2. 模型預設就錯 | 倒垃圾 dǎo | `_MODEL_MISREADINGS` |

A 完全自動；B1 由 g2pW 表 diff 導出；**B2 沒有任何工具能預測**，只能實測撞到才加一行。

## A 類：判準是「詞組讀音 ≠ 單字預設讀音」

不是「這個字是不是破音字」——`pypinyin` 認定的破音字有 8,617 字，在真實業務文字裡佔
50.4%，對它們全部注入等於全句注入拼音。改為比較兩個讀音：只有上下文真的改變了讀音才注入。
這繞過了破音字集合這個不可靠的輸入，問的不是「這個字有幾種讀法」而是「上下文有沒有改變
它的讀法」。

**注入面極小，所以壓平模型原生韻律的風險趨近於零。** `tests/data/real_dialogues.json`
客戶側 19 turn 過 TN 後有 2,305 個漢字，A 類注入 7 字（**0.30%**，漢字為分母），逐筆核對
全對；同一份文字被 B1 表鎖掉 22 字，三類合計 29 個標記。往後改判準都要用這份 fixture
重量——自造樣本（40 句、823 字）在它上面 22/22 全對，卻驗不出真實文字的 4 處誤鎖。

**加一個破音字不必編輯任何清單**，這正是它取代 `和`／`倒` 那三份手打前後文清單的理由：
那三份每一份都是編輯判斷、沒有真值，而錯的方式是靜默的。

## B1 類：只鎖差異字，不做全句轉拼音

鎖一個本來就唸對的字，是我方新增的錯誤；不鎖只是保留模型的預設。這個不對稱決定了本模組
的所有取捨——寧可漏鎖，不可誤鎖。全句轉拼音還會壓平模型原生的韻律（未實測），且錯音無從
定位；只鎖差異字則「鎖定集合就是嫌疑集合」。

## 表怎麼來的

台灣讀音全部取自 **g2pW 發行模型 `G2PWModel-v2-onnx`（內含 `version` 檔記為 v3.0）** 的
`MONOPHONIC_CHARS.txt` 與 `POLYPHONIC_CHARS.txt`，取每字的首選讀音，經 `g2pw` 套件內的
`bopomofo_to_pinyin_wo_tune_dict.json` 轉成拼音（`ㄌㄜ4` → `le4`，格式與 VoxCPM2 的 `{le4}`
零轉換）；大陸基準取自 `pypinyin`（詞組感知，`Style.TONE3`，輕聲補 5）。兩者不同者才進表。

**沒有產表腳本進版控**，那兩份 .txt 在 589 MB 的模型 zip 內，一支跑不起來的腳本只會變成
另一份會漂移的記載（`pypinyin` 自 #50 起已是相依，但缺的一直是那兩份 .txt）。要重建或
擴充這份表，照上面兩段做即可——zip
不必整包下載，它的中央目錄在檔尾，以 HTTP Range 讀 EOCD 後只取那兩個成員（各約 100 KB）。

**注意不要拿 g2pw 套件內的 `char_bopomofo_dict.json` 當台灣表**——它是通用 fallback，實測
`垃`=ㄌㄚ1、`企`=ㄑㄧ3，是大陸讀音。台灣值只在發行模型的那兩份 .txt 裡。

## 選字是策展的，讀音是機器導出的

機器 diff 出來是 811 字，其中絕大多數是罕用字（乇、乿、仩…）——它們的讀音來自同一份表，
但沒有人會去核對數百筆罕用字，而未核對的強制讀音就是未爆彈；`圳` 更是反例，它的台灣讀音
是 zùn，鎖了它「深圳」就唸錯。故選字以「會出現在保險業務對話裡」為準，表刻意小、依實測
證據成長。表內每一筆的台灣值都經人工核對（研究 #15 抽驗的 13 字全數吻合）。

## char 級優先，詞級只給真的兩讀的字

**能 char 級鎖就 char 級鎖。** 逐詞鎖同一個字會讓它在同一句裡出現兩種讀音：`期` 只收
「期限」而漏掉「長期」時，「長期照護的保障期限」會唸出兩個不同的 `期`，比全部不鎖更刺耳。

char 級的條件是「在現代台灣文本中實質上只有一個讀音」，比 g2pW 的 MONOPHONIC 略寬——`期`
在表中是破音字，但另一讀 ㄐㄧ1 只用於「期年」這類古語。每一筆的判斷寫在該行的註解裡。

**詞級只留下真的有兩個活讀音的字**：`質` 在台灣是品質 zhí、人質 zhì，char 級強制必然破壞
一邊。而詞級沒有詞界意識，`產品質量` 會被切成 `產|品質|量`，故要有排除清單。
"""

import re
from functools import lru_cache

from opencc import OpenCC
from pypinyin import Style, pinyin

from vibe_vox.tts_text import SpeechText

# 連續漢字區段。**位置對應只能在這種區段上做**：`pinyin()` 把連續的非漢字（`LINE`、
# `21,600`）合併成單一 token，回傳長度因此不等於字元數，用索引直接對應會在真實文字上
# 錯位。非漢字本來就切斷詞，逐段處理不丟失該有的上下文。
_HAN_RUN = re.compile(r"[一-鿿]+")


@lru_cache(maxsize=1)
def _to_simplified() -> OpenCC:
    """繁→簡轉換器。OpenCC 載入字典成本高，故單例、延遲初始化（同 adapters/zh.py）。"""
    return OpenCC("t2s")


@lru_cache(maxsize=None)
def _default_reading(char: str) -> str:
    """這個字單獨出現時的讀音。每次合成會查數千次，故快取。

    不設上限：鍵是單一漢字，集合有界（CJK 基本區 20,992 字，實務上遠低於此），設了只是
    在常用字之間互相驅逐。
    """
    return pinyin(char, style=Style.TONE3, neutral_tone_with_five=True)[0][0]


# 結構助詞與語氣詞，**永遠不注入**（#50 D5）。
#
# 這條是真實逐字稿抓出來的，自造樣本（40 句 823 字、22/22 全對）驗不出：第一次跑真實文字
# 時 12 個注入點錯了 4 個，全落在這裡——`的`→dì、`的`→dí、`喔`→wō ×2。排除之後同一份
# 文字 7/7 全對。**`的` 是中文最高頻的字**，把它唸成 dì 比漏鎖任何實詞都刺耳。
#
# 代價是 `了解`／「沒完沒了」的 liǎo、`著急` 的 zháo 這類實詞用法一併漏掉。依本模組
# 「寧可漏鎖，不可誤鎖」的紅線接受：這些字在訓練資料裡的出現次數遠超任何實詞。
#
# **放寬這份清單要用 `tests/data/real_dialogues.json` 重驗**，自造樣本量不到它守的東西。
_PARTICLES = frozenset("的了著地得嗎呢吧啊呀喔哦噢嗯耶欸啦囉唷哇咧嘛喲唄")


def _syllable(reading: str) -> str:
    """去掉聲調的音節。

    注入只在**聲母或韻母改變**時發生（#50 D4）。純聲調的差異有三類，三類都留給模型：
    輕聲（`個` gè→ge、`思` sī→si）是口語現象；規則變調（`一` yī→yí、`不` bù→bú 在去聲
    前變陽平）是模型自己處理的規則，而那兩個字都是超高頻，鎖死只會讓語流變僵；剩下的是
    真正的破音區分（`差別` chā／chà、`有空` kòng／kōng、`部分` fèn／fēn）。

    **第三類是刻意漏鎖，理由是實測。** 放寬到聲調也注入，在 `tests/data/real_dialogues.json`
    上 A 類注入從 7 字升到 20 字（2,305 個漢字裡的 0.30%→0.87%），多出的 13 筆有 10 筆
    正確、**3 筆誤鎖**：「倒是」唸成 dǎo shì、「當期」唸成 dàng qí、「一通電話」唸成
    yī tòng。而那 10 筆修的是模型可能本來就唸對的字——誤鎖是我方新增的確定錯音，漏鎖
    只是保留模型預設。

    推翻條件：實測顯示模型在該漏鎖處唸錯而聽得出來。那時要逐筆進 `_MODEL_MISREADINGS`，
    不是放寬本條——上一段的 3 筆誤鎖會一起回來。
    """
    return reading.rstrip("12345")


def _context_readings(text: str) -> dict[int, str]:
    """回整段文字的「原文位置 → 讀音」，只收上下文真的改變了讀音的字。

    逐個連續漢字區段處理再把段內位置平移回全文（分段的理由見 `_HAN_RUN`）。
    """
    return {
        run.start() + offset: reading
        for run in _HAN_RUN.finditer(text)
        for offset, reading in _readings_changed_by_context(run.group()).items()
    }


def _readings_changed_by_context(original: str) -> dict[int, str]:
    """回一個連續漢字區段內的「段內位置 → 讀音」。無法可靠對應時回空 dict。

    `pypinyin` 的 `phrases_dict` 47,111 條全是簡體（實測 `銀行` ✗／`银行` ✓），繁體
    輸入一條都命中不了、只能逐字 fallback，故查詢前先轉簡體。繁→簡是多對一、字數不變，
    讀音因此可按位置套回繁體——**但那是通則不是保證**，長度對不上就整段放棄，保持原狀
    優於錯位注入。
    """
    simplified = _to_simplified().convert(original)
    if len(simplified) != len(original):
        return {}
    phrase_readings = pinyin(simplified, style=Style.TONE3, neutral_tone_with_five=True)
    if len(phrase_readings) != len(simplified):
        return {}

    changed: dict[int, str] = {}
    for offset, (char, simple, in_phrase) in enumerate(
        zip(original, simplified, phrase_readings, strict=True)
    ):
        # 助詞比對的是**原文**字元：清單以繁體書寫供人核對，而輸入本就是繁體。簡體輸入
        # 落到判準只會拿到該字的正確讀音（`着急` zhao2），不是誤鎖。
        if char in _PARTICLES:
            continue
        reading = in_phrase[0]
        if _syllable(reading) != _syllable(_default_reading(simple)):
            changed[offset] = reading
    return changed


# 讀音與大陸預設不同，且在現代台灣文本中實質單一讀音的字。括號內是判斷依據。
_CHAR_READINGS = {
    "垃": "le4",  # 表中為單音字
    "圾": "se4",  # 表中另有 ㄐㄧ2，但「圾」在現代中文只出現在「垃圾」
    "企": "qi4",  # 表中為單音字（企業、企圖、企鵝）
    "髮": "fa3",  # 表中為單音字（頭髮、髮型、理髮）
    "息": "xi2",  # 表中為單音字（消息、利息、休息）
    "期": "qi2",  # 表中另有 ㄐㄧ1，只用於「期年」這類古語
    "液": "yi4",  # 表中另有 ㄧㄝ4，那是大陸讀音
    "攜": "xi1",  # 表中另有 ㄒㄧㄝ2，那是大陸讀音
    "績": "ji1",  # 表中為單音字（業績、成績、功績）；簡體 `绩` 在大陸一律 jì
}

# 真的有兩個活讀音的字，只能逐詞鎖。讀音取自 g2pW 表的台灣首選讀音。
_WORD_READINGS = {
    "品質": ("pin3", "zhi2"),
    "體質": ("ti3", "zhi2"),
}

# **詞級沒有詞界意識**，故要明列會跨過真實詞界的更長詞。它們與詞表放進同一個交替式並原樣
# 通過，最長匹配優先就自動把短的擋掉。
#
# 這是「寧可漏鎖」的具體形式：`品質量測` 不在此列，故它照鎖（那裡的品質確實是一個詞）。
_NOT_LOCKED = ("產品質量", "身體質量")

# **交替分支的順序即優先序**，故長度遞減排序，理由有三：排除詞要蓋過它所包含的詞
# （產品質量 vs 品質）、詞要蓋過字（品質 vs 質，否則只鎖到一半）。同長度者互不重疊，
# 彼此順序無影響。
_TARGET = re.compile(
    "|".join(
        re.escape(target)
        for target in sorted(
            (*_NOT_LOCKED, *_WORD_READINGS, *_CHAR_READINGS), key=len, reverse=True
        )
    )
)

# **模型預設就唸錯的字**（#50 D8 的 B2 類）：觸發詞 → 該詞**首字**的讀音，其餘字留給
# 上面兩張表（`倒垃圾` 的 `垃圾` 仍走 char 級變成 `{le4}{se4}`）。
#
# 「倒」的傾倒義在台灣唸 dào（倒垃圾、倒杯水），跌倒義唸 dǎo（倒閉、跌倒、顛倒）。**這不是
# 兩岸差異**——大陸同樣分兩讀——而是模型選錯了那一讀，操作者實際聽到「倒垃圾」被唸成 dǎo
# （2026-08-08 聽測）。#50 的判準也看不到它：pypinyin 對「倒垃圾」的預設本來就是 dào，詞組
# 讀音等於單字預設，delta 為零。**這一類只能實測撞到才知道**，沒有任何工具能事先預測模型
# 會在哪裡唸錯。
#
# **它與 #50 刪掉的那幾份清單性質不同**：每筆是一行資料（詞 → 讀音），不是一份要人維護的
# 前後文規則。加一筆的成本是一行，而不是一輪審查。
#
# **涵蓋率刻意極低。** 詞級沒有詞界意識，所以連 `倒車`、`倒數` 都不能收：「他跌倒車上」
# 「他跌倒數次」都是自然句，兩字詞照樣跨界（第一版用「後接字白名單加前接動詞排除」，審查
# 實測出七句誤鎖，那七句現在是 `test_the_falling_dao_is_never_locked` 的安全網）。只收三字
# 且前面接任何跌倒義動詞都語意不通的搭配。`倒水`、`把垃圾倒掉` 因此漏鎖——漏鎖保留模型
# 預設，誤鎖是我方新增的錯音，這個不對稱優先於涵蓋率。
#
# 讀音 dào 取自**教育部辭典的語義區分**，不是 g2pW 表的首選——那份表給的是字級首選讀音，
# 對兩讀都活的字幫不上。
#
# **只有第一筆有聽測證據**，另兩筆是 #46 隨它一起進來的同語義搭配。#50 D8 寫「`倒垃圾`
# 是目前唯一一筆」，那句是在描述准入條件（實測才加），不是要求刪掉已經在唸對的字——鎖
# `倒杯水` 的風險是零（dào 本來就是它的正確讀音），刪掉才是把已交付的行為改回錯的。
_MODEL_MISREADINGS = {
    "倒垃圾": "dao4",  # 操作者 2026-08-08 實測聽到唸成 dǎo
    "倒杯水": "dao4",
    "倒杯茶": "dao4",
}
# 長度遞減，理由同 `_TARGET`：交替分支的順序即優先序，長的要蓋過它所包含的短詞。目前三筆
# 等長故順序無影響，但下一筆不見得。
_MISREAD = re.compile(
    "|".join(re.escape(word) for word in sorted(_MODEL_MISREADINGS, key=len, reverse=True))
)


def _table_readings(text: str) -> dict[int, str]:
    """回 B1 與 B2 兩類表算出的「原文位置 → 讀音」，每一筆讀音都經人工核對。

    與 `_context_readings` 一樣**在原文上比對**，這是 #50 D6 換掉「三趟字串改寫」的理由：
    每一趟注入的標記都會破壞下一趟的詞界判斷（舊版 `倒` 必須跑在主表之前，就是因為主表
    會把 `倒垃圾` 的 `垃圾` 換成標記，讓「後接哪一個字」這個判準看不到字）。位置各自算完
    再一次注入，順序就不再是正確性的一部分。
    """
    found: dict[int, str] = {}
    for match in _TARGET.finditer(text):
        matched = match.group()
        if matched in _NOT_LOCKED:
            continue
        readings = _WORD_READINGS.get(matched) or (_CHAR_READINGS[matched],)
        for offset, reading in enumerate(readings):
            found[match.start() + offset] = reading
    for match in _MISREAD.finditer(text):
        found[match.start()] = _MODEL_MISREADINGS[match.group()]
    return found


def _inject(text: str, readings: dict[int, str]) -> str:
    return "".join(
        f"{{{readings[index]}}}" if index in readings else char
        for index, char in enumerate(text)
    )


def lock_taiwan_readings(text: str) -> SpeechText:
    """回已鎖定讀音的合成文字。沒有要鎖的字詞時回原文（仍是 SpeechText）。

    回 `SpeechText` 而非 `str`：`{}` 是 VoxCPM2 的讀音標記語法，而使用者文字裡的大括號
    必須被中性化（見 tts_text）。型別是「這串已由前處理層處理完、可以原樣送出」這件事的
    載體——沒有它，`Utterance` 的 validator 分不出我方的標記與使用者的注入，只能一律轉全形
    而把讀音標記一起毀掉。

    **判準與表正交，且都在原文上計算後一次注入。** 判準處理 A 類（上下文改變讀音），表
    處理 B1（兩岸讀音不同）與 B2（模型預設就錯），兩邊不重疊——表裡那些字的詞組讀音等於
    單字預設，判準看不到它們。真的撞在同一個位置時**表勝出**：它是兩岸差異的權威，而且
    每一筆都經過人工核對。
    """
    readings = _context_readings(text)
    readings.update(_table_readings(text))
    return SpeechText(_inject(text, readings))
