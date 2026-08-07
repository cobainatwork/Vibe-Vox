"""台灣讀音鎖定，seam 為 tts_g2p 的公開函式。

**期望值不從實作推導**：每個台灣讀音都取自 g2pW 發行模型（內含 version 檔記為 v3.0）的
讀音表，該表的 13 個抽驗值已與研究 #15 的宣稱逐字核對過；大陸基準取自 pypinyin。產法與
選字準則見 tts_g2p 的模組 docstring。

契約層面的行為（實際送進模型的字串、使用者的大括號仍被中性化）測在 test_tts.py 的
HTTP seam。
"""

import re

import pytest

from vibe_vox.tts_g2p import lock_taiwan_readings


@pytest.mark.parametrize(
    ("raw", "locked"),
    [
        # spike #11 驗過的標竿詞：模型預設會唸成 lā jī、xīng qī、qǐ yè。
        ("垃圾", "{le4}{se4}"),
        ("我們把垃圾分類做得很好", "我們把{le4}{se4}分類做得很好"),
        ("星期三見", "星{qi2}三見"),
        ("企業主", "{qi4}業主"),
        # 保險業務對話的常見詞。
        ("這張保單的品質", "這張保單的{pin3}{zhi2}"),
        ("繳費期限是什麼時候", "繳費{qi2}限是什麼時候"),
        ("液體會理賠嗎", "{yi4}體會理賠嗎"),
        ("記得攜帶身分證", "記得{xi1}帶身分證"),
        ("這是好消息", "這是好消{xi2}"),
        ("頭髮", "頭{fa3}"),
    ],
)
def test_words_and_chars_whose_taiwan_reading_differs_are_locked(raw, locked):
    assert lock_taiwan_readings(raw) == locked


def test_the_same_character_gets_one_reading_across_a_sentence():
    """同一個字在同一句裡不能有兩種讀音。

    這是逐詞鎖的失效模式：`期` 只收「期限」而漏掉「長期」時，這句會唸出兩個不同的 `期`，
    比全部不鎖更刺耳。`期` 因此是 char 級（模組 docstring 記了判斷依據）。
    """
    assert lock_taiwan_readings("長期照護的保障期限是二十年") == (
        "長{qi2}照護的保障{qi2}限是二十年"
    )
    # 保險對話最高頻的 `期` 詞一次全涵蓋。
    assert lock_taiwan_readings("定期壽險可以分期繳，期滿或逾期未繳") == (
        "定{qi2}壽險可以分{qi2}繳，{qi2}滿或逾{qi2}未繳"
    )


@pytest.mark.parametrize(
    ("raw", "locked"),
    [
        # 連接詞在台灣唸 hàn。
        ("我和您約下週三", "我{han4}您約下週三"),
        ("把保單內容和費率說清楚", "把保單內容{han4}費率說清楚"),
        # 動詞／形容詞用法仍是 hé，前後那一個字就是判準。
        ("世界和平", "世界和平"),
        ("他個性溫和", "他個性溫和"),
        ("和解書", "和解書"),
        ("一團和氣", "一團和氣"),
    ],
)
def test_the_conjunction_he_is_read_han_but_the_word_uses_are_not(raw, locked):
    # **這一筆的選字是編輯判斷**：表的台灣首選是 he2，han4 是第二讀音；教育部把 hé 列為
    # 正讀、hàn 列為又讀。依據是 spike #11 的聽測與 #46 D4 指定的作法。
    assert lock_taiwan_readings(raw) == locked


@pytest.mark.parametrize(
    ("raw", "locked"),
    [
        # 傾倒義在台灣唸 dào，而模型預設是 dǎo（操作者實際聽到的錯音就是「倒垃圾」）。
        ("你要去倒垃圾", "你要去{dao4}{le4}{se4}"),
        ("幫我倒杯水", "幫我{dao4}杯水"),
        ("先倒杯茶給客戶", "先{dao4}杯茶給客戶"),
    ],
)
def test_the_pouring_dao_is_locked(raw, locked):
    # 讀音取自教育部辭典的語義區分，不是 g2pW 表的字級首選——那份表對兩讀都活的字幫不上。
    assert lock_taiwan_readings(raw) == locked


@pytest.mark.parametrize(
    "untouched",
    [
        # **這一組是 `倒` 這條規則的安全網，每一句都是審查實測出來的誤鎖。**
        #
        # 第一版用「後接字白名單加前接動詞排除」，這些句子全部被鎖成 dào。根因是「跌倒義的
        # 前接動詞」是開放集合（撲醉暈栽癱嚇病累撞滑吹…），封閉列舉蓋不住，故改為詞級。
        "他撲倒水裡",
        "他醉倒車上",
        "她暈倒車廂裡",
        "他栽倒水溝",
        "他撞倒車庫的門",
        "客戶滑倒水溝裡",
        "颱風把樹吹倒油行門口",
        # 連兩字詞都不能收：這兩句是自然句，`倒車`／`倒數` 進清單就會誤鎖。
        "他跌倒車上",
        "他跌倒數次",
        # 跌倒義的一般用法。
        "公司倒閉了",
        "他不小心跌倒了",
        "顛倒是非",
        "壓倒性的優勢",
        # **刻意漏鎖**：唸成 dǎo 是保留模型預設，而收 `倒水` 會誤鎖上面那幾句。
        "麻煩你倒水",
    ],
)
def test_the_falling_dao_is_never_locked(untouched):
    # 誤鎖是我方新增的錯音，漏鎖只是保留模型預設。這個不對稱優先於涵蓋率。
    assert lock_taiwan_readings(untouched) == untouched


def test_the_pouring_dao_is_missed_when_the_word_order_changes():
    """操作者抱怨的那個動詞換個語序就漏鎖，這是「寧可漏鎖」的實際代價而不是 bug。

    `垃圾` 仍被 char 級規則鎖住，只有 `倒` 回退到模型預設——半句對半句是原狀，而不是
    半句對半句錯。
    """
    assert lock_taiwan_readings("把垃圾倒掉") == "把{le4}{se4}倒掉"


def test_the_pouring_dao_is_judged_before_the_main_table_rewrites_its_context():
    """`倒` 的判準是它後面接什麼詞，而主表會把 `垃圾` 換成標記讓那個判準看不到字。

    順序倒置的症狀很窄也很難看：只有「倒垃圾」這一種搭配會唸成 dǎo lè sè，半句對半句錯。
    """
    assert lock_taiwan_readings("倒垃圾") == "{dao4}{le4}{se4}"


@pytest.mark.parametrize(
    "untouched",
    [
        # **這一組是這個模組的安全網。** 鎖錯比不鎖更糟：唸錯一個本來就對的詞，是我方
        # 新增的錯誤，而不鎖只是保留模型的預設。
        #
        # `質` 在台灣是兩讀（品質 zhí、人質 zhì），故它只能逐詞鎖、不能 char 級鎖。
        "人質",
        # 詞級沒有詞界意識：這兩句的「品質」「體質」都跨過真實詞界（產品｜質量、身體｜
        # 質量），鎖了就是把兩個詞的邊界唸錯。
        "產品質量",
        "身體質量指數",
        # 沒有任何要鎖的字。
        "您好，我想了解一下這張保單的內容",
    ],
)
def test_text_without_locked_readings_is_returned_unchanged(untouched):
    assert lock_taiwan_readings(untouched) == untouched


def test_a_real_word_still_locks_when_it_is_not_one_of_the_excluded_phrases():
    # 排除清單列的是「跨過詞界的那幾串」，不是「含量字就不鎖」。`品質量測` 的品質確實是
    # 一個詞，該鎖。
    assert lock_taiwan_readings("品質量測") == "{pin3}{zhi2}量測"


def test_longest_match_wins_so_a_word_beats_the_characters_inside_it():
    # 交替分支的順序即優先序。字先命中的話 `品質` 只會鎖到一半；排除詞若後於它所包含的
    # 詞，`產品質量` 又會被鎖。
    assert lock_taiwan_readings("品質") == "{pin3}{zhi2}"
    assert lock_taiwan_readings("產品質量") == "產品質量"


def test_every_table_entry_is_internally_consistent():
    """表是手工編輯的，而編輯失誤在執行期是靜默的。

    音節數與詞長不符會讓那個詞少唸或多唸一個音；讀音格式錯了（少聲調、大寫、注音沒轉成
    拼音）模型不會報錯，只會照字面唸出來。兩者都聽得出來但都不會有任何錯誤訊號。
    """
    from vibe_vox.tts_g2p import (
        _CHAR_READINGS,
        _NOT_LOCKED,
        _POUR_DAO_WORDS,
        _WORD_READINGS,
    )

    for word, readings in _WORD_READINGS.items():
        assert len(readings) == len(word), f"{word} 的音節數與字數不符"

    for reading in [*_CHAR_READINGS.values(), *sum(_WORD_READINGS.values(), ())]:
        assert re.fullmatch(r"[a-z]+[1-5]", reading), f"{reading} 不是拼音加聲調"

    # char 級的字不該出現在任何詞條裡：詞在交替式中先命中，那個字級的項就是死資料，而
    # 死資料會讓下一個人以為某個字被涵蓋了。
    for ch in _CHAR_READINGS:
        inside = [w for w in _WORD_READINGS if ch in w]
        assert not inside, f"{ch} 已被詞條 {inside} 涵蓋，字級的那一筆是死資料"

    # 每個排除詞都必須真的包含一個詞條，否則它擋不到任何東西，只是一筆看起來有用的資料。
    for phrase in _NOT_LOCKED:
        assert any(w in phrase for w in _WORD_READINGS), f"{phrase} 沒有蓋住任何詞條"

    # `倒` 的清單寫成完整的詞供人核對，但比對時只吃 `倒` 本身、後綴當成 lookahead。不以
    # `倒` 起頭的詞會產出錯誤的 lookahead，而那是執行期靜默的。
    for word in _POUR_DAO_WORDS:
        assert word.startswith("倒"), f"{word} 不以「倒」起頭，lookahead 會切錯"
        assert len(word) >= 3, f"{word} 只有兩個字，兩字詞已實測會跨詞界誤鎖"
