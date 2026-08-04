"""對齊結果的合理性檢查與合併：seam 為 alignment 模組的公開函式，直接呼叫。

強制對齊無容錯機制，轉錄文字有誤時會靜默對歪（ADR-0004），故每段須經檢查。
測試用的異常型態取自 #26 的真實實測案例，不是假想的。
"""

from vibe_vox.adapters.base import Segment, Word
from vibe_vox.alignment import is_sane, merge_alignment


def test_zero_duration_word_fails_check():
    # #26 以官方測試音訊實測到的真實案例：「幾」的 Start 與 End 相同。此異常出現在
    # qwen-asr 的 fix_timestamp 之後——該函式修單調性，不修對齊正確性（ADR-0004）。
    words = [
        Word(Text="幾", Start=2.32, End=2.32),
        Word(Text="乎", Start=2.48, End=2.72),
    ]

    assert not is_sane(words, span=0.4, bounds=(0.0, 4.204))


def test_normal_chinese_pace_passes_check():
    # #26 實測的正常字時長為 0.16–0.40 秒（ADR-0004）。判準若比這嚴，每段都會被
    # 誤判為對齊失敗，評分端反而拿不到本可用的資料。
    words = [
        Word(Text="甚", Start=0.40, End=0.56),
        Word(Text="至", Start=0.56, End=0.96),
        Word(Text="出", Start=0.96, End=1.20),
    ]

    assert is_sane(words, span=0.85, bounds=(0.0, 4.204))


def test_overlong_word_fails_check():
    # 對歪的典型表現：模型把字塞到錯誤位置，該字吃掉整段靜音。
    words = [
        Word(Text="好", Start=0.40, End=0.56),
        Word(Text="的", Start=0.56, End=9.80),
    ]

    assert not is_sane(words, span=10.0, bounds=(0.0, 12.0))


def test_reversed_timestamps_fail_check():
    # qwen-asr 的 fix_timestamp 以最長遞增子序列修單調性，但它修的是單調性不是
    # 對齊正確性（ADR-0004），且我方不得假設上游一定修好。後字早於前字結束即
    # 表示對齊已失序。
    words = [
        Word(Text="你", Start=1.00, End=1.20),
        Word(Text="好", Start=0.80, End=1.00),
    ]

    assert not is_sane(words, span=0.4, bounds=(0.0, 4.0))


def test_span_check_skipped_when_not_applicable():
    # span 為 None 表示該段不適用跨距判準（首段與末段，見 merge_alignment）。
    words = [
        Word(Text="王", Start=0.40, End=0.62),
        Word(Text="安", Start=0.62, End=0.85),
    ]

    assert is_sane(words, span=None, bounds=(0.0, 40.07))


def test_span_far_shorter_than_segment_fails_check():
    # 票的第三項判準：段內首末時間與該段音訊長度偏離過大。對歪的典型表現是模型把
    # 整段的字擠在開頭一小段——每個字的時長都正常、順序也單調，只有跨距不對，故
    # 前兩項判準抓不到它。
    words = [
        Word(Text="王", Start=0.40, End=0.62),
        Word(Text="安", Start=0.62, End=0.85),
        Word(Text="蓮", Start=0.85, End=1.10),
    ]

    assert not is_sane(words, span=39.57, bounds=(0.0, 40.07))


def test_span_covering_most_of_segment_passes_check():
    # VibeVoice 是窮盡連續切分，段內幾乎滿是語音（實測相鄰段間隙 0–0.66 秒，多數
    # 為 0，ADR-0004），故正常段的跨距應接近段長。
    words = [
        Word(Text="王", Start=0.40, End=0.62),
        Word(Text="明", Start=38.90, End=39.20),
    ]

    assert is_sane(words, span=39.57, bounds=(0.0, 40.07))


def test_words_within_buffer_region_pass_check():
    # 切片左右各留 buffer（#27），邊界字落在 buffer 區內是合法的——bounds 已含
    # buffer，故不可以段界本身為判準，否則邊界字會使整段被誤判。
    # 段界 9.8–10.3、buffer 0.5，故 bounds 為 9.3–10.8。首字自 9.70 起，早於段界。
    words = [
        Word(Text="你", Start=9.70, End=9.90),
        Word(Text="好", Start=9.90, End=10.20),
    ]

    assert is_sane(words, span=0.5, bounds=(9.3, 10.8))


def test_empty_words_fail_check():
    # #27 的 adapter 對退化段落回空清單（空 Content、切片落在音檔外）。無從驗證
    # 即不可信，且下游無法據以重算段界。
    assert not is_sane([], span=4.0, bounds=(0.0, 4.0))


def test_merge_recomputes_segment_bounds_from_words():
    # aligned 的段落，Start／End 改為首字 Start 與末字 End：原切點語義為空且已實際
    # 誤導判讀（ADR-0004）。重算後段間間隙才是句間停頓。
    segments = [Segment(Start=0.0, End=1.4, Speaker="0", Content="甚至出")]
    words = [
        [
            Word(Text="甚", Start=0.40, End=0.56),
            Word(Text="至", Start=0.56, End=0.96),
            Word(Text="出", Start=0.96, End=1.20),
        ]
    ]

    aligned, _ = merge_alignment(
        segments, words, audio_duration=4.204, slice_buffer=0.5
    )

    assert aligned[0].aligned is True
    assert aligned[0].Start == 0.40
    assert aligned[0].End == 1.20
    assert aligned[0].Content == "甚至出"  # 文字不因對齊而變
    assert len(aligned[0].words) == 3


def test_merge_degrades_only_the_failing_segment():
    # 第一層降級：未通過的段落回退切點時間戳、words 空、aligned false，其餘段照常
    # （ADR-0004）。逐字稿有獨立價值，不因一段對歪而整份失效。
    segments = [
        Segment(Start=0.0, End=1.0, Speaker="0", Content="甚至"),
        Segment(Start=2.0, End=2.8, Speaker="0", Content="幾乎"),
    ]
    words = [
        [Word(Text="甚", Start=0.40, End=0.56), Word(Text="至", Start=0.56, End=0.96)],
        [Word(Text="幾", Start=2.32, End=2.32), Word(Text="乎", Start=2.48, End=2.72)],
    ]

    aligned, _ = merge_alignment(segments, words, audio_duration=4.0, slice_buffer=0.5)

    assert aligned[0].aligned is True
    assert (aligned[0].Start, aligned[0].End) == (0.40, 0.96)
    assert aligned[1].aligned is False
    assert aligned[1].words == []
    assert (aligned[1].Start, aligned[1].End) == (2.0, 2.8)  # 退回切點，不是字級


def test_summary_counts_only_aligned_segments():
    # 未對齊段的時間戳是切點語義，混入彙總會使 speech_start／speech_end 失去
    # 「實際發音邊界」的意義。docs/api/asr.md §4.5 亦要求消費端若採 aligned_duration
    # 以外的分母須自行排除未對齊段。
    segments = [
        Segment(Start=0.0, End=1.0, Speaker="0", Content="甚至"),
        Segment(Start=2.0, End=2.8, Speaker="0", Content="幾乎"),
    ]
    words = [
        [Word(Text="甚", Start=0.40, End=0.56), Word(Text="至", Start=0.56, End=0.96)],
        [Word(Text="幾", Start=2.32, End=2.32), Word(Text="乎", Start=2.48, End=2.72)],
    ]

    _, summary = merge_alignment(segments, words, audio_duration=4.5, slice_buffer=0.5)

    assert summary.audio_duration == 4.5  # 音檔實際總長，非 Segment End 最大值
    assert summary.speech_start == 0.40
    assert summary.speech_end == 0.96
    assert summary.aligned_duration == 0.56  # 僅第一段的 0.96 − 0.40


def test_merge_exempts_first_and_last_segment_from_span_check():
    # 頭尾沉默是**預期情境**而非故障：開頭沉默（按下錄音後不敢開口）本身即話術
    # 缺失、結尾沉默有兩種語義，兩者都必須保留給消費端判斷（ADR-0004）。它們會
    # 壓低首末段的跨距比，若套跨距判準就會把正常段標為對齊失敗、丟棄其 words，
    # 而那正是消費端唯一能算出頭尾沉默的資料。
    segments = [
        Segment(Start=0.0, End=35.0, Speaker="0", Content="開頭沉默二十秒後才開口"),
        Segment(Start=35.0, End=70.0, Speaker="0", Content="中間段講滿"),
        Segment(Start=70.0, End=105.0, Speaker="0", Content="講完後忘記按停止"),
    ]
    words = [
        # 首段：語音自 20 秒起，跨距比僅 0.41
        [Word(Text="開", Start=20.5, End=20.9), Word(Text="口", Start=34.4, End=34.8)],
        # 中間段：跨距接近段長
        [Word(Text="講", Start=35.4, End=35.8), Word(Text="滿", Start=69.2, End=69.6)],
        # 末段：15 秒講完，之後全是沉默，跨距比僅 0.43
        [Word(Text="停", Start=70.4, End=70.8), Word(Text="止", Start=85.0, End=85.4)],
    ]

    aligned, summary = merge_alignment(
        segments, words, audio_duration=110.0, slice_buffer=0.5
    )

    assert [s.aligned for s in aligned] == [True, True, True]
    assert summary.speech_start == 20.5  # 開頭沉默 20.5 秒，完整保留
    assert summary.speech_end == 85.4  # 結尾沉默 110.0 − 85.4 = 24.6 秒


def test_merge_never_aligns_zero_length_segment():
    # 模型輸出缺欄位時 Start 與 End 同補 0.0（docs/api/asr.md §6）。該段的切片只有
    # buffer 區的音訊，卻配整段話的文字，對齊結果必然無意義——但每個字的時長、
    # 順序與範圍都可能正常，前三項判準攔不到它。零長度段落本身即退化訊號。
    segments = [Segment(Start=0.0, End=0.0, Speaker="0", Content="你好")]
    words = [
        [Word(Text="你", Start=0.0, End=0.25), Word(Text="好", Start=0.25, End=0.5)]
    ]

    aligned, summary = merge_alignment(
        segments, words, audio_duration=30.0, slice_buffer=0.5
    )

    assert aligned[0].aligned is False
    assert aligned[0].words == []
    assert summary.speech_start is None  # 不得產出 0.0 這種假的「沒有開頭沉默」


def test_summary_is_structurally_complete_when_no_speech():
    # 學員全程未發話（音訊有效但無語音，docs/api/asr.md §6）：欄位結構完整回傳、
    # 值為 null 或 0，不報錯也不省略欄位。評分端據此視為零分或無效作答。
    aligned, summary = merge_alignment([], [], audio_duration=12.5, slice_buffer=0.5)

    assert aligned == []
    assert summary.audio_duration == 12.5  # 音檔仍有長度
    assert summary.speech_start is None
    assert summary.speech_end is None
    assert summary.aligned_duration == 0.0
    assert set(summary.model_dump()) == {
        "audio_duration",
        "speech_start",
        "speech_end",
        "aligned_duration",
    }
