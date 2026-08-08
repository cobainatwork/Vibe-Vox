"""TN（書面→口語）的規則表，seam 為 tts_tn 的公開函式。

案例表測在模組 seam 而非 HTTP seam：規則有數十條，逐條走一次 HTTP 只是把同一件事測慢。
契約層面的行為（實際送進模型的字串、長度上限量在哪）測在 test_tts.py 的 HTTP seam。

**期望值不從實作推導。** 每條的正確答案是「台灣人會怎麼唸」，來源是規格 #46 的問題敘述與
docs/api/tts.md §7 對消費端的承諾，不是跑一次程式看它輸出什麼。
"""

import pytest

from vibe_vox.tts_tn import to_spoken_form


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        ("0", "零"),
        ("7", "七"),
        ("10", "十"),
        ("11", "十一"),
        ("20", "二十"),
        ("99", "九十九"),
        ("100", "一百"),
        ("101", "一百零一"),
        ("110", "一百一十"),
        ("999", "九百九十九"),
        ("1000", "一千"),
        ("1001", "一千零一"),
        ("1010", "一千零一十"),
        ("1250", "一千二百五十"),
        ("10000", "一萬"),
        ("10001", "一萬零一"),
        ("35000", "三萬五千"),
        ("100000000", "一億"),
    ],
)
def test_integers_are_read_aloud(raw, spoken):
    # 零的插入規則是這條的重點：一千零一 而非 一千一，一百一十 而非 一百十。
    assert to_spoken_form(raw) == spoken


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        ("1.8", "一點八"),
        ("0.5", "零點五"),
        ("3.14", "三點一四"),
        ("1,250", "一千二百五十"),
        ("1,250,000", "一百二十五萬"),
        ("-5", "負五"),
        ("１２３", "一百二十三"),
        ("身高1.8公尺", "身高一點八公尺"),
        ("共1,250元整", "共一千二百五十元整"),
    ],
)
def test_numbers_in_running_text_are_read_aloud(raw, spoken):
    # 小數點後逐位唸（三點一四 而非 三點十四）：小數部分不是一個整數，沒有位名。
    # 千分位逗號是書面標記，唸的時候不存在。全形數字要先折半，否則後面每條規則都要
    # 認兩套字元。
    assert to_spoken_form(raw) == spoken


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        ("3kg", "三公斤"),
        ("1.8m", "一點八公尺"),
        ("180cm", "一百八十公分"),
        ("5km", "五公里"),
        ("500ml", "五百毫升"),
        ("3L", "三公升"),
        ("15KG", "十五公斤"),
    ],
)
def test_units_use_taiwan_vocabulary(raw, spoken):
    # 千克／米 是大陸用詞，OpenCC 的字形轉換修不掉（實測 s2tw 與 s2twp 對「三千克」原樣
    # 輸出），故必須在這一層就產出台灣用詞。
    assert to_spoken_form(raw) == spoken


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        ("2026年", "二零二六年"),
        ("1990年代", "一九九零年代"),
        ("民國115年", "民國一百一十五年"),
        ("民國 115 年 8 月 5 日", "民國一百一十五年八月五日"),
        ("3年前", "三年前"),
    ],
)
def test_years_are_read_digit_by_digit_except_after_minguo(raw, spoken):
    # 四位數的西元年逐位唸（二零二六年），不是基數（兩千零二十六年）。民國年相反——它是
    # 基數（一百一十五年），因為那個數字是「第幾年」而不是一組年號數字。
    assert to_spoken_form(raw) == spoken


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        ("2026/8/5", "二零二六年八月五日"),
        ("2026-08-05", "二零二六年八月五日"),
        ("8月5日", "八月五日"),
    ],
)
def test_full_dates_are_read_as_dates(raw, spoken):
    # 前導零不唸（08 月是八月）。這條必須比分數規則早——否則 `2026/8/5` 會變成分數。
    assert to_spoken_form(raw) == spoken


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        ("3:05", "三點零五分"),
        ("15:30", "十五點三十分"),
        ("9:00", "九點整"),
    ],
)
def test_clock_times_are_read_as_times(raw, spoken):
    # 分鐘小於十要帶零（三點零五分），整點唸「整」。這條必須比比例規則早，否則 `3:05`
    # 會變成「三比零五」。
    assert to_spoken_form(raw) == spoken


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        ("0912-345-678", "零九一二、三四五、六七八"),
        ("02-2345-6789", "零二、二三四五、六七八九"),
    ],
)
def test_phone_numbers_are_read_digit_by_digit_with_pauses(raw, spoken):
    # 電話要逐位唸並在段落間停頓，否則聽的人記不下來。頓號是給模型的停頓提示。
    # 台灣的 1 唸「一」（「幺」是大陸讀法）。
    assert to_spoken_form(raw) == spoken


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        ("第2名", "第二名"),
        ("第 3 天", "第三天"),
        ("第10屆", "第十屆"),
    ],
)
def test_ordinals_use_er_not_liang(raw, spoken):
    # zh-CN 框架在此產出「第两名」。這條同時是下一條規則（兩）的護欄：序數位置永遠是二。
    assert to_spoken_form(raw) == spoken


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        # 小數點後的位不是量詞前的「二」。`_NUMERALS` 少了「點」時，一點二公斤 會變成
        # 一點兩公斤——而 1.2kg 是報價單上最常見的寫法之一。
        ("1.2kg", "一點二公斤"),
        ("12.2元", "十二點二元"),
        ("1.2倍", "一點二倍"),
        ("2.5kg", "兩點五公斤"),  # 首位的二仍是兩
        # 序數位置永遠是二，含四位以上的序數。
        ("第2000大", "第二千大"),
    ],
)
def test_two_stays_er_after_a_decimal_point_and_after_di(raw, spoken):
    assert to_spoken_form(raw) == spoken


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        ("重量 3 kg", "重量三公斤"),
        ("15－20%", "百分之十五到二十"),
        ("15-20%", "百分之十五到二十"),
        ("20~30%", "百分之二十到三十"),
        ("交期 7-10 天", "交期七到十天"),
        ("會議 10:00~11:30", "會議十點整到十一點三十分"),
        ("NT$8800 年繳", "新臺幣八千八百元年繳"),
        ("0912345678", "零九一二、三四五、六七八"),
        ("15％", "百分之十五"),
        ("1：30", "一點三十分"),
        ("2026／8／5", "二零二六年八月五日"),
        ("10/20 交貨", "十月二十日交貨"),
        ("進度 3/4", "進度四分之三"),
    ],
)
def test_rules_survive_the_forms_people_actually_type(raw, spoken):
    """規則之間的順序與全形寫法造成的漏接。

    每一條都是靜默失效：不回錯誤、不進 log，只是唸錯。`NT$8800 年繳` 尤其糟——年份規則
    先吃掉數字後，`NT$` 失去可比對的數字而原樣留下，模型會把它當字母唸。
    """
    assert to_spoken_form(raw) == spoken


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        # **口語的拉長音不是範圍。** 真實逐字稿裡每四句就出現一次（見
        # tests/fixtures/real_dialogues.json），而「你好～我是」被唸成「你好到我是」時
        # 整句就毀了，且完全靜默。
        ("哈囉你好～我是白蘿蔔", "哈囉你好～我是白蘿蔔"),
        ("哈～不用這麼客氣啦", "哈～不用這麼客氣啦"),
        ("欸欸～這麼嚴肅幹嘛啦", "欸欸～這麼嚴肅幹嘛啦"),
        ("輕鬆一點喔～再見啦", "輕鬆一點喔～再見啦"),
        ("好啦～三點見", "好啦～三點見"),
        # 這條規則的本意：時刻與日期規則已經把範圍兩端換成中文，留下的波浪號要唸「到」。
        ("會議 10:00~11:30", "會議十點整到十一點三十分"),
        ("3:05~4:15", "三點零五分到四點十五分"),
        ("2026/8/5～2026/9/1", "二零二六年八月五日到二零二六年九月一日"),
        ("三～五天", "三到五天"),
    ],
)
def test_a_drawn_out_tilde_in_speech_is_not_a_range(raw, spoken):
    """判準是「左邊是數字或時間量詞」，不是「兩邊都是漢字」。

    後者曾經成立過，因為這條規則只被時刻範圍的案例驗過——而口語的拉長音同樣夾在漢字
    之間。真實逐字稿一進 repo 就抓到它（#50）。
    """
    assert to_spoken_form(raw) == spoken


def test_a_realistic_sales_sentence_comes_out_whole():
    """一句真實的業務語句，一次跨十條規則。

    逐條的案例表看不出規則之間的互相破壞——`NT$36,000` 與 `20 年` 相鄰時年份規則若排在
    台幣之前，數字會先被換掉而讓 `NT$` 原樣留下被當字母唸。這條是那類失效的偵測網。
    """
    spoken = to_spoken_form(
        "您好，這份保單年繳 NT$36,000，保障 20 年，投保年齡 30-45 歲。"
        "體重 3kg 以下的嬰兒不在範圍內，室溫請維持 25℃。"
        "會議改到 10/20 下午 2:00，達成率 15-20%，我的手機是 0912345678。"
    )

    assert spoken == (
        "您好，這份保單年繳新臺幣三萬六千元，保障二十年，投保年齡三十到四十五歲。"
        "體重三公斤以下的嬰兒不在範圍內，室溫請維持攝氏二十五度。"
        "會議改到十月二十日下午兩點整，達成率百分之十五到二十，我的手機是零九一二、三四五、六七八。"
    )


def test_a_very_long_digit_run_is_read_not_crashed():
    # 位名只到「兆」，超出的組會 IndexError 而冒成 500——而 500 不在契約 §6 的錯誤表內，
    # 消費端的錯誤處理分支涵蓋不到。長數字串本來就該逐位唸（人也是這樣唸卡號的）。
    assert to_spoken_form("1" * 17) == "一" * 17


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        ("200元", "兩百元"),
        ("2000人", "兩千人"),
        ("20000", "兩萬"),
        ("2個", "兩個"),
        ("2公斤", "兩公斤"),
        ("2小時", "兩小時"),
        ("2:05", "兩點零五分"),
    ],
)
def test_quantity_two_is_liang(raw, spoken):
    # 台灣的量詞前與百／千／萬／億的首位唸「兩」。這條在通用數字規則之後跑，故它處理的是
    # 中文數字而不是阿拉伯數字。
    assert to_spoken_form(raw) == spoken


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        ("1,250", "一千二百五十"),
        ("第2名", "第二名"),
        ("20", "二十"),
        ("12:00", "十二點整"),
        ("2月", "二月"),
        ("2號", "二號"),
        ("2樓", "二樓"),
        ("2026年", "二零二六年"),
    ],
)
def test_two_stays_er_where_liang_would_be_wrong(raw, spoken):
    # **這一條比上一條重要**：zh-CN 框架的錯誤方向正是多唸了「兩」（第两名、两千零二十六
    # 年）。序數、十位、月／號／樓、年號數字一律是二；`1,250` 的二百在數字中段，契約
    # §7 逐字寫的就是「一千二百五十」。
    assert to_spoken_form(raw) == spoken


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        ("重量 3kg", "重量三公斤"),
        ("共 1,250 元", "共一千二百五十元"),
        ("室溫 25℃ 很舒服", "室溫攝氏二十五度很舒服"),
        ("iPhone 15 Pro", "iPhone 十五 Pro"),
        ("他說 hello 了", "他說 hello 了"),
    ],
)
def test_spaces_between_chinese_are_dropped(raw, spoken):
    # 規則替換後會留下夾在中文之間的空白（wetext 就是這樣產出「第 兩名」「新臺幣 三 萬元」
    # 的）。空白不發音，故拿掉沒有損失；但**只拿掉兩側都是漢字的那些**——拉丁文字兩側的
    # 空白是詞界，拿掉會讓 `iPhone 十五 Pro` 變成一個難以斷開的長 token。
    assert to_spoken_form(raw) == spoken


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        ("NT$1,250", "新臺幣一千二百五十元"),
        ("NT$ 1,250", "新臺幣一千二百五十元"),
        ("總價NT$1,250元", "總價新臺幣一千二百五十元"),
        ("NT$35,000", "新臺幣三萬五千元"),
    ],
)
def test_new_taiwan_dollar_is_spelled_out(raw, spoken):
    # `docs/api/tts.md` §7 逐字承諾了這條（`NT$1,250` → 新臺幣一千二百五十元）。原樣進
    # 模型時 `NT$` 會被當字母唸。已經帶了「元」的輸入不能再補一個。
    assert to_spoken_form(raw) == spoken


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        ("15%", "百分之十五"),
        ("0.5%", "百分之零點五"),
        ("達成率15%", "達成率百分之十五"),
        ("3/4", "四分之三"),
        ("1:1.5", "一比一點五"),
        ("10~20", "十到二十"),
    ],
)
def test_ratios_and_ranges_read_in_reading_order(raw, spoken):
    # 分數的唸法把分母搬到前面（四分之三），故這條規則得重新排列而不只是替換符號。
    assert to_spoken_form(raw) == spoken


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        ("25℃", "攝氏二十五度"),
        ("25°C", "攝氏二十五度"),
        ("-5℃", "攝氏負五度"),
        ("100℉", "華氏一百度"),
        ("室溫25℃很舒服", "室溫攝氏二十五度很舒服"),
    ],
)
def test_temperature_puts_the_scale_first(raw, spoken):
    # 台灣的語序是「攝氏二十五度」，zh-CN 框架產出的是「二十五攝氏度」——語序相反，
    # 這不是字形問題也不是用詞問題，字典類的後處理修不掉。
    assert to_spoken_form(raw) == spoken
