"""對齊結果的合理性檢查與合併（ADR-0004 的兩層降級之第一層）。

強制對齊**無容錯機制**：訓練以 MFA pseudo-label，假設轉錄文字與音訊完全對應。
文字含亂碼、漏字或多字時，模型會硬把字塞到某個時間位置而**不報錯**。沒有這層
檢查，對歪的時間戳會以「看似正常」的形式流到評分端。

**判準分兩層，這個區分是本模組的核心**：

- **結構性缺陷**（時間戳逆轉、落在音訊範圍外、對齊跨距不足、零長度段落）使整段
  不可信。它們表示這一段的對齊結構壞了，而非個別字的誤差。
- **單字時長異常是局部雜訊**，只在**佔比過高**時才代表系統性對歪。#26 的 13 字
  樣本就含一個零時長，故它是模型的常態輸出而非故障訊號。

零容忍會讓長段落幾乎必然被攔：單字異常率若為 1%，190 字的段落至少出現一個的機率
是 85%，40 字只有 34%。實測正是如此（#34）：9 段中只有 33 與 42 字的兩段通過，其餘
5.94 至 39.59 秒、最長約 190 字的段落全數被攔，而該錄音語音乾淨、辨識品質良好、
語速正常。丟棄 189 個可用的時間戳來排除 1 個可疑的，代價遠大於收益。

缺陷以 `Defect` 回傳而非布林：`aligned: false` 不說明為什麼，接手者無從判斷是模型
對歪還是判準過嚴。`code` 為穩定的機器可讀值（#32 要統計各判準的攔下次數），`detail`
給人看。兩者都會寫進 log。

log 用 `warning` 而非 `info` **不是隨意的**：本專案沒有 logging 設定，uvicorn 預設
只配置自己的 logger，故 `vibe_vox.*` 的 effective level 是 WARNING、root 無 handler。
`info()` 會被靜默丟棄，診斷等於沒有。

三個推算閾值待實測校準（#32）。
"""

import logging
from dataclasses import dataclass

from pydantic import BaseModel

from vibe_vox.adapters.base import Segment, Word

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Defect:
    """使某段對齊不可信的缺陷。

    code 為穩定的機器可讀值，供 #32 統計各判準的攔下次數；detail 給人看，含具體的
    字與數值。改 code 的字面值會破壞既有的統計，視同契約變更。
    """

    code: str
    detail: str

# 單字時長的下界，即模型的時間解析度（config.json 的 timestamp_segment_time，
# 80ms，ADR-0004）。小於一個解析度單位的時長模型無法真實產生，只能是退化輸出；
# #26 實測到的零時長「幾」即落在此界之下。
#
# **落在界外不使該段失敗**，只累計佔比，見 MAX_IMPLAUSIBLE_DURATION_RATIO。
MIN_WORD_SECONDS = 0.08

# 單字時長的上界。中文語速約每分鐘 200–300 字（ADR-0004），即每字 0.2–0.3 秒；
# 2.0 秒為其 7–10 倍，含拖長音的正常發音達不到，而對歪時單字會吃掉整段靜音、
# 遠超此值。
MAX_WORD_SECONDS = 2.0

# 對齊跨距（末字 End − 首字 Start）至少須達該段切點跨距的這個比例。
#
# 抓的是前兩項判準漏掉的型態：模型把整段的字擠在開頭一小段——每個字時長正常、
# 順序也單調，只有跨距不對。VibeVoice 是窮盡連續切分，段內幾乎滿是語音（實測相鄰
# 段間隙 0–0.66 秒、19 個中 6 個為 0，單語者連續語音時 9 個全為 0，ADR-0004），故
# 正常段的比例應遠高於 0.5。取這個寬鬆的下界是刻意的：誤判的代價是把本可用的段落
# 標為不可信，使評分端反而拿不到資料，故寧可漏判輕微異常。
MIN_SPAN_RATIO = 0.5

# 單字時長異常的容許佔比。超過此比例才判定整段不可信：那不再是局部雜訊，而是
# 系統性對歪（例如模型把整段的字均勻壓縮）。
#
# 取 0.3 的依據：#26 那 13 字樣本的異常率為 7.7%（1／13），是目前唯一的實測值，
# 且該樣本極小、應視為最壞情況；0.3 是其約四倍，正常段落達不到。系統性對歪則會
# 使多數字同時落在界外，遠超此值。**此值未經實測校準**（#34 定值，併入 #32 校準）。
MAX_IMPLAUSIBLE_DURATION_RATIO = 0.3


class AlignedSegment(Segment):
    """對齊後的 Segment。既有四欄形狀不變，加兩欄（向後相容）。

    aligned 為 false 時 words 為空、且 Start／End **維持切點語義**——兩者語義不同，
    混用會得到錯誤結果，故消費端必須顯式檢查 aligned 而不以 words 是否為空代替
    （空陣列會被誤讀為「該段沒有字」，ADR-0004）。
    """

    aligned: bool
    words: list[Word]


class AlignmentSummary(BaseModel):
    """供消費端自行組出評分分母的四個數字（docs/api/asr.md §4.5）。

    **不預先排除任何區間**：開頭沉默本身即話術缺失，排除它等於允許學員先發呆；
    結尾沉默有「講完忘記按停止」與「講不下去」兩種語義，音訊上無從區分，該規則
    只能由消費端依其他訊號決定（ADR-0004）。

    學員全程未發話時 speech_start／speech_end 為 null，結構仍完整回傳而非報錯。
    """

    audio_duration: float
    speech_start: float | None
    speech_end: float | None
    aligned_duration: float


def merge_alignment(
    segments: list[Segment],
    words_per_segment: list[list[Word]],
    *,
    audio_duration: float,
    slice_buffer: float,
    aligner_failed: bool = False,
) -> tuple[list[AlignedSegment], AlignmentSummary]:
    """合併對齊結果、逐段檢查、重算段界，並算出四個彙總數字。

    未通過檢查的段落回退切點時間戳並標記，其餘段照常——ADR-0004 的兩層降級之
    第一層。

    `aligner_failed` 表示對齊服務整體不可得（不可用或逾時）。**刻意不叫「上游」**：本
    模組同時討論 ASR 模型的輸出品質，那個詞在此會歧義成 VibeVoice。此時全段的 words
    必然為空，逐段記錄只會產生 N 條完全相同的訊息（#36 實測 63 條）而把端點層那條真正
    的原因推出畫面。段落仍照常標記為未對齊，只是不重複記錄同一件事（#37）。
    """
    last = len(segments) - 1
    merged: list[AlignedSegment] = []
    # strict：筆數不符即 Protocol 被違約。靜默截短會讓尾端 Segment 從回應消失，使
    # segments 與 transcription_only 互相矛盾，比直接失敗更難察覺。
    for index, (segment, words) in enumerate(
        zip(segments, words_per_segment, strict=True)
    ):
        defect = _find_segment_defect(
            segment,
            words,
            span_applies=0 < index < last,
            audio_duration=audio_duration,
            slice_buffer=slice_buffer,
        )
        if defect is not None and not aligner_failed:
            # warning 而非 info：本專案無 logging 設定，info 會被靜默丟棄（見模組
            # docstring）。對齊服務可用時逐段都記，包含空清單，因為那可能是「該段全是
            # 標點」或 adapter 剔除了退化段落與非語音標記段這種真正需要知道的情形
            # （#34、#38）。服務整體失敗則全段同因，由端點層記一條即可（#37）。
            logger.warning(
                "第 %d 段未通過對齊檢查（%s）：%s", index + 1, defect.code, defect.detail
            )
        merged.append(_with_alignment(segment, words, aligned=defect is None))
    return merged, _summarize(merged, audio_duration=audio_duration)


def _find_segment_defect(
    segment: Segment,
    words: list[Word],
    *,
    span_applies: bool,
    audio_duration: float,
    slice_buffer: float,
) -> Defect | None:
    """找出該段對齊的缺陷；可信則回 `None`。

    零長度段落一律不可信：模型輸出缺欄位時 `Start` 與 `End` 同補 0.0
    （docs/api/asr.md §6），該段的切片只有 buffer 區的音訊卻配整段話的文字，對齊
    結果必然無意義，但每個字的時長、順序與範圍都可能正常，逐字判準攔不到它。
    零長度本身即退化訊號。

    **首段與末段不套跨距判準**（`span_applies`）：頭尾沉默是預期情境而非故障。
    開頭沉默（按下錄音後不敢開口）本身即話術缺失、結尾沉默有「講完忘記按停止」與
    「講不下去」兩種語義，兩者都必須保留給消費端判斷（ADR-0004）。它們會壓低首末
    段的跨距比，若套跨距判準就會把正常段標為對齊失敗、丟棄其 words，而那正是消費端
    唯一能算出頭尾沉默的資料。中間段沒有這個問題：窮盡連續切分下，中間段的首字與
    末字之間就是該段的全部內容，段內停頓已被跨距涵蓋，故其比例應接近 1。

    代價是首末段的擠壓型對歪抓不到。單段錄音（既首亦末）則完全不套。記於 #32。
    """
    span = segment.End - segment.Start
    if span <= 0:
        return Defect(
            "zero_length_segment",
            f"段落本身為零長度（Start 與 End 同為 {segment.Start:.2f}）",
        )
    return find_word_defect(
        words,
        span=span if span_applies else None,
        bounds=_bounds(
            segment, audio_duration=audio_duration, slice_buffer=slice_buffer
        ),
    )


def find_word_defect(
    words: list[Word], *, span: float | None, bounds: tuple[float, float]
) -> Defect | None:
    """找出使該段對齊不可信的缺陷；無缺陷回 `None`。

    span 為該段的切點跨距（`End − Start`），`None` 表示該段不適用跨距判準（首段與
    末段，理由見 `_find_segment_defect`）。bounds 為該段切片在原音檔的時間範圍。

    空清單即缺陷：無從驗證，且下游無法據以重算段界。
    """
    if not words:
        return Defect("empty_words", "字級清單為空")

    structural, implausible = _scan_words(words, bounds=bounds)
    # 時長佔比一律附進 detail，即使真正的缺陷是結構性的：#32 要的是各段的佔比分布，
    # 若結構缺陷讓該段直接早退，那批資料就永遠收不到。
    ratio = len(implausible) / len(words)
    ratio_note = f"（單字時長異常佔比 {ratio:.0%}，{len(implausible)}／{len(words)} 字）"

    if structural is not None:
        return Defect(structural.code, structural.detail + ratio_note)

    if ratio > MAX_IMPLAUSIBLE_DURATION_RATIO:
        sample = "、".join(
            f"「{w.Text}」{w.End - w.Start:.2f} 秒" for w in implausible[:3]
        )
        return Defect(
            "implausible_duration_ratio",
            f"單字時長異常佔比 {ratio:.0%}（{len(implausible)}／{len(words)} 字）"
            f"超過上限 {MAX_IMPLAUSIBLE_DURATION_RATIO:.0%}，例如 {sample}",
        )

    if span is not None and (aligned_span := words[-1].End - words[0].Start) < (
        span * MIN_SPAN_RATIO
    ):
        return Defect(
            "insufficient_span",
            f"對齊跨距 {aligned_span:.2f} 秒不足該段 {span:.2f} 秒的 "
            f"{MIN_SPAN_RATIO:.0%}，字被擠在一小段內" + ratio_note,
        )
    return None


def _scan_words(
    words: list[Word], *, bounds: tuple[float, float]
) -> tuple[Defect | None, list[Word]]:
    """掃一遍：回傳第一個結構缺陷（若有）與所有時長異常的字。

    刻意掃完全部而非遇到結構缺陷就返回，這樣時長佔比恆可得（見 `find_word_defect`）。
    """
    lower, upper = bounds
    structural: Defect | None = None
    implausible: list[Word] = []
    previous_end: float | None = None

    for word in words:
        duration = word.End - word.Start
        # 局部雜訊，只累計（見模組 docstring 的分層說明）。
        if duration < MIN_WORD_SECONDS or duration > MAX_WORD_SECONDS:
            implausible.append(word)
        if structural is None and previous_end is not None and word.Start < previous_end:
            structural = Defect(
                "reversed_timestamps",
                f"時間戳順序逆轉：「{word.Text}」自 {word.Start:.2f} 起，"
                f"早於前字結束的 {previous_end:.2f}",
            )
        if structural is None and (word.Start < lower or word.End > upper):
            structural = Defect(
                "out_of_slice_bounds",
                f"時間戳落在該段音訊範圍外：「{word.Text}」"
                f"{word.Start:.2f}–{word.End:.2f} 不在 {lower:.2f}–{upper:.2f} 內",
            )
        previous_end = word.End

    return structural, implausible


def _bounds(
    segment: Segment, *, audio_duration: float, slice_buffer: float
) -> tuple[float, float]:
    """該段切片在原音檔涵蓋的時間範圍，與 #27 的切片夾限一致。

    含 buffer 是必要的：邊界字可能落在 buffer 區內（切點非發音邊界），以段界本身
    為判準會使每個邊界字都讓整段被誤判。
    """
    return (
        max(0.0, segment.Start - slice_buffer),
        min(audio_duration, segment.End + slice_buffer),
    )


def _with_alignment(
    segment: Segment, words: list[Word], *, aligned: bool
) -> AlignedSegment:
    """把對齊結果套到 Segment 上。

    aligned 時段界改為首字 Start／末字 End；否則保留原切點並丟棄 words——未通過
    檢查的時間戳不得外流，留著會被誤用。
    """
    recomputed = {"Start": words[0].Start, "End": words[-1].End} if aligned else {}
    return AlignedSegment(
        **(segment.model_dump() | recomputed),
        aligned=aligned,
        words=words if aligned else [],
    )


def _summarize(
    segments: list[AlignedSegment], *, audio_duration: float
) -> AlignmentSummary:
    """四個彙總數字只計 aligned 的段落：未對齊段的時間戳是切點語義，混入會使
    speech_start／speech_end 失去「實際發音邊界」的意義。"""
    trusted = [s for s in segments if s.aligned]
    return AlignmentSummary(
        audio_duration=audio_duration,
        speech_start=min((s.Start for s in trusted), default=None),
        speech_end=max((s.End for s in trusted), default=None),
        aligned_duration=round(sum(s.End - s.Start for s in trusted), 3),
    )
