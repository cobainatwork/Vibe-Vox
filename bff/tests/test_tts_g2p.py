"""讀音鎖定，seam 為 tts_g2p 的公開函式。

**期望值不從實作推導。** 表內的台灣讀音取自 g2pW 發行模型（內含 version 檔記為 v3.0）的
讀音表，該表的 13 個抽驗值已與研究 #15 的宣稱逐字核對過；#50 判準注入的那些讀音逐筆對
教育部辭典核對過（銀行 yín háng、給付 jǐ fù、出差 chū chāi…）。產法與選字準則見 tts_g2p
的模組 docstring。

**實作在 #50 整個換掉了，本檔的斷言除了行為真的改變的那些之外一條都沒動**——那是「測試
斷言送進模型的字串、不斷言判準的內部形狀」這個定義是否成立的檢驗。真的改變的有兩處，
各有一條測試記載理由：`和` 交還給標準讀音（`he_is_left_to_the_model…`），以及聲調差異
一律漏鎖（`a_word_whose_reading_differs_only_in_tone…`）。

契約層面的行為（實際送進模型的字串、使用者的大括號仍被中性化）測在 test_tts.py 的
HTTP seam，真實逐字稿的回歸網也在那裡。
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


@pytest.mark.parametrize(
    ("raw", "locked"),
    [
        # 模型預設唸 yín xíng，操作者聽得出來。真實逐字稿裡 7 個注入點有 5 個是這個字。
        ("我要去銀行", "我要去銀{hang2}"),
        ("我在分行等您", "我在分{hang2}等您"),
        # 保險業務對話的常見詞，讀音逐筆人工核對過。
        ("繳費很有彈性", "繳費很有{tan2}性"),
        ("幫您調整保額", "幫您{tiao2}整保額"),
        ("給付條件是什麼", "{ji3}付條件是什麼"),
        ("我下週出差", "我下週出{chai1}"),
        ("錢會還給您", "錢會{huan2}給您"),
        ("背景音樂太大聲", "背景音{yue4}太大聲"),
        ("我們會重新安排", "我們會{chong2}新安排"),
        # **非漢字不得讓位置對應錯位。** `pinyin()` 把連續非漢字合併成單一 token，回傳
        # 長度因此不等於字元數；純中文樣本量不到這件事，真實逐字稿（`加個 LINE`、
        # `21,600`）一跑就炸。故只對連續漢字區段做對應。
        ("去銀行辦 LINE Pay", "去銀{hang2}辦 LINE Pay"),
        ("加個 LINE 好嗎", "加個 LINE 好嗎"),
    ],
)
def test_a_reading_the_context_changes_is_locked(raw, locked):
    # 判準是「詞組讀音 ≠ 單字預設讀音」：上下文沒改變讀音的字一個都不碰。真實逐字稿
    # 2622 字只注入 7 字（0.27%），所以壓平模型原生韻律的風險趨近於零。
    assert lock_taiwan_readings(raw) == locked


@pytest.mark.parametrize(
    "untouched",
    [
        # **這一組是刻意漏鎖**，每一句的詞組讀音都與單字預設只差聲調，被 D4 擋下：
        # 差別 chā／chà、有空 kòng／kōng、部分 fèn／fēn、商量 liáng／liàng、
        # 划算 huá／huà、變更 gēng／gèng。放寬 D4 的實測代價記在 `tts_g2p._syllable`。
        "兩者的差別在哪",
        "您什麼時候有空",
        "這部分的保障",
        "我要跟家人商量",
        "這樣比較划算",
        "要變更受益人",
        # 漏鎖的另一個成因：**繁體詞查不到詞組**。`身分` 的簡體是「身份」而 OpenCC t2s
        # 只轉字形不改詞，`身分證` 因此查不到 fèn 的詞組讀音。同一句的 `攜` 走 B1 表，
        # 不受影響——半句鎖住半句原狀，而不是半句對半句錯。
        "身分證字號",
    ],
)
def test_a_word_whose_reading_differs_only_in_tone_is_left_alone(untouched):
    assert lock_taiwan_readings(untouched) == untouched


@pytest.mark.parametrize(
    "untouched",
    [
        # 輕聲：`個` gè→ge、`思` sī→si。口語輕聲模型多半自己會，注入只是無謂的密度。
        "這個",
        "你的意思是",
        # 規則變調：`一` yī→yí、`不` bù→bú 在去聲前變陽平，那是模型自己處理的規則。
        # 兩者都只改聲調，故本條一併涵蓋——規格 D3 因此不必是獨立的排除清單。
        "一年",
        "不是",
        "一定要",
    ],
)
def test_a_change_of_tone_alone_is_not_locked(untouched):
    # 注入只在聲母或韻母改變時發生。兩者都是超高頻字，鎖死只會讓語流變僵。
    assert lock_taiwan_readings(untouched) == untouched


@pytest.mark.parametrize(
    "untouched",
    [
        # **這一組是真實逐字稿抓出來的，自造樣本驗不出**（理由與實測見 `tts_g2p._PARTICLES`）。
        "喔喔喔",
        "嗯嗯我知道了",
        # **代價**：`了解` 的 liǎo、「沒完沒了」的 liǎo、「著急」的 zháo 這類實詞用法一併
        # 漏掉，依「寧可漏鎖，不可誤鎖」的紅線接受。
        "我想了解一下這張保單",
        "他很著急",
    ],
)
def test_particles_are_never_locked(untouched):
    """`的` 這個最重要的案例守不在這裡。

    它要整段 turn 的上下文才會誤鎖——`你的意思是` 這五個字單獨跑不會，從同一句切出來的
    任何短片段也不會。守得住它的只有 test_tts.py 的
    `test_real_dialogue_turns_survive_the_whole_preprocessing_pipeline`，而那條必須逐次
    計數才有效（存在性斷言抓不到「四個 `的` 少一個」）。
    """
    assert lock_taiwan_readings(untouched) == untouched


def test_a_character_pypinyin_has_no_reading_for_never_becomes_a_marker():
    """字典查不到的字不得變成 `{兙5}` 這種非法標記。

    這是本模組最糟的失效模式：模型收到一個它不認得的標記，而我方沒有任何錯誤訊號。CJK
    基本區 20,992 字裡有 68 個是這種字（`兙`、`嗧`、`龦`…），pypinyin 對它們回原字元
    加聲調 5。

    **目前不可觸發**，因為 pypinyin 詞組查不到就逐字退回單字表，兩邊拿到同一個 fallback
    而 delta 恆為零。這條把那個性質釘住——它是 pypinyin 的內部行為，不是我們的契約。
    """
    locked = lock_taiwan_readings("我要去兙嗧龦銀行")

    assert locked == "我要去兙嗧龦銀{hang2}"
    for marker in re.findall(r"\{[^{}]*\}", locked):
        assert re.fullmatch(r"\{[a-z]+[1-5]\}", marker), f"{marker} 不是合法讀音標記"


def test_the_same_character_gets_one_reading_across_a_sentence():
    """同一個字在同一句裡不能有兩種讀音。

    這是逐詞鎖的失效模式：`期` 只收「期限」而漏掉「長期」時，這句會唸出兩個不同的 `期`，
    比全部不鎖更刺耳。`期` 因此是 char 級（模組 docstring 記了判斷依據）。
    """
    # `長` 由 #50 的判準補上（cháng，單字預設是 zhǎng），兩個機制在同一句裡各管各的。
    assert lock_taiwan_readings("長期照護的保障期限是二十年") == (
        "{chang2}{qi2}照護的保障{qi2}限是二十年"
    )
    # 保險對話最高頻的 `期` 詞一次全涵蓋。
    assert lock_taiwan_readings("定期壽險可以分期繳，期滿或逾期未繳") == (
        "定{qi2}壽險可以分{qi2}繳，{qi2}滿或逾{qi2}未繳"
    )


@pytest.mark.parametrize(
    "untouched",
    [
        # 連接詞用法：**曾經鎖成 hàn，#50 起交還給標準讀音 hé。**
        "我和您約下週三",
        "把保單內容和費率說清楚",
        # 動詞／形容詞用法本來就是 hé。
        "世界和平",
        "他個性溫和",
        "和解書",
        "一團和氣",
    ],
)
def test_he_is_left_to_the_model_now_that_its_context_lists_are_gone(untouched):
    """`和` 不再被鎖，這是 #50 唯一一項操作者聽得出來的回退。

    鎖 hàn 是編輯判斷而非標準讀音（教育部把 hé 列正讀、hàn 列又讀），所以 #50 的判準
    看不到它——pypinyin 給的詞組讀音與單字預設都是 hé，delta 為零。要保留 hàn 只能回到
    手打的前後文清單，而那正是本票要廢掉的東西（#50 D7）。

    連接詞的前後文是**開放集合**，這正是 `倒` 那一輪實測出七句誤鎖的同一個根因。
    """
    assert lock_taiwan_readings(untouched) == untouched


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
    # **#50 的判準看不到這一類。** pypinyin 對「倒垃圾」的預設本來就是 dào（正確），是模型
    # 自己唸成 dǎo——詞組讀音等於單字預設，delta 為零。故它留在一張逐筆加的資料表裡。
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


def test_the_pouring_dao_and_the_main_table_both_read_the_original_text():
    """`倒` 的判準是它後面接什麼詞，而主表要在同一串字裡把 `垃圾` 換成標記。

    舊版是三趟字串改寫，`倒` 必須跑在主表之前——主表一旦把 `垃圾` 換掉，「後接哪一個詞」
    就看不到字了。症狀很窄也很難看：只有「倒垃圾」這一種搭配會唸成 dǎo lè sè。#50 D6
    改為各自在**原文**上算位置、最後一次注入，順序因此不再是正確性的一部分。
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
        _MODEL_MISREADINGS,
        _NOT_LOCKED,
        _WORD_READINGS,
    )

    for word, readings in _WORD_READINGS.items():
        assert len(readings) == len(word), f"{word} 的音節數與字數不符"

    for reading in [
        *_CHAR_READINGS.values(),
        *sum(_WORD_READINGS.values(), ()),
        *_MODEL_MISREADINGS.values(),
    ]:
        assert re.fullmatch(r"[a-z]+[1-5]", reading), f"{reading} 不是拼音加聲調"

    # char 級的字不該出現在任何詞條裡：詞在交替式中先命中，那個字級的項就是死資料，而
    # 死資料會讓下一個人以為某個字被涵蓋了。
    for ch in _CHAR_READINGS:
        inside = [w for w in _WORD_READINGS if ch in w]
        assert not inside, f"{ch} 已被詞條 {inside} 涵蓋，字級的那一筆是死資料"

    # 每個排除詞都必須真的包含一個詞條，否則它擋不到任何東西，只是一筆看起來有用的資料。
    for phrase in _NOT_LOCKED:
        assert any(w in phrase for w in _WORD_READINGS), f"{phrase} 沒有蓋住任何詞條"

    # B2 表鎖的是**詞的首字**，其餘字留給上面兩張表。
    for word in _MODEL_MISREADINGS:
        assert len(word) >= 3, f"{word} 只有兩個字，兩字詞已實測會跨詞界誤鎖"
        # 首字若同時在 char 表裡，兩張表對同一個位置各給一個讀音，而合併時 B2 靜默勝出。
        assert word[0] not in _CHAR_READINGS, f"{word} 的首字已由 char 表管轄"
