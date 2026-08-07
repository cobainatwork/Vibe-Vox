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

**閾值的校準狀態（#32）**：單字時長下界由模型的時間解析度導出，已經實測驗證；上界
在真人語音樣本中從未觸發，仍是推算值；異常佔比與跨距下界亦為推算值，後者對多語者
會議的簡短應答段有已知的誤判（#40）。

**一個會重演的陷阱**：閾值若剛好等於被量化資料的格點，比較結果由浮點誤差決定而非由
語義決定。下界原本設在格點上，使 216 個正常字被誤判為異常，見 DEGENERATE_WORD_SECONDS。
"""

import logging
from dataclasses import dataclass

from pydantic import BaseModel

from vibe_vox.adapters.base import Omission, Segment, SegmentAlignment, Word

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Defect:
    """使某段對齊不可信的缺陷。

    code 為穩定的機器可讀值，供 #32 統計各判準的攔下次數；detail 給人看，含具體的
    字與數值。改 code 的字面值會破壞既有的統計，視同契約變更。
    """

    code: str
    detail: str

# 模型的時間解析度（config.json 的 timestamp_segment_time，ADR-0004）。短於此值的字長
# 不可能是真實量測，只能是退化輸出或後處理的產物，故它就是單字時長的下界。
#
# **時間戳並不落在此值的格點上**：實測 `4.19 / 0.08 = 52.375`。多數字的時長是它的整數倍
# （1941 字中 392 個恰為一單位），但後處理也會產生 0.04、0.048 這類非整數倍的值。
WORD_TIME_RESOLUTION_SECONDS = 0.08

# 比較時長與上述下界的容差，**這條是必要的而非防禦性的**。
#
# 時間戳相減會帶 1e-13 量級的尾數，故直接寫 `duration < 0.08` 會把同一種時長判到兩邊：
# `4.27 - 4.19 = 0.07999999999999918` 被攔，而 `6.11 - 6.03 = 0.08000000000000007` 沒被攔。
# 真機實測 1941 字中有 392 個恰好一單位，216 個因此被誤判為異常，佔全部「異常」的 54.5%
# （2026-08-05，#32）。
#
# **曾試過把下界改設在格點之間（0.04）來迴避，那是錯的**：0.04 本身就是資料裡出現的值
# （`453.13 - 453.09 = 0.040000000000020464` 與 `453.21 - 453.17 = 0.03999999999996362`），
# 同一個缺陷原地重演。閾值不能設在資料實際出現的值上，只能對它做容差比較。
#
# 1e-6 秒遠大於浮點尾數、又遠小於任何有意義的時長差（最接近下界的實測值是 0.048，距離
# 0.032 秒），故每個實際出現的時長都能得到穩定的判定。
_DURATION_TOLERANCE_SECONDS = 1e-6

# **落在界外不使該段失敗**，只累計佔比，見 MAX_IMPLAUSIBLE_DURATION_RATIO。

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
    alignments: list[SegmentAlignment],
    *,
    audio_duration: float,
) -> tuple[list[AlignedSegment], AlignmentSummary]:
    """合併對齊結果、逐段檢查、重算段界，並算出四個彙總數字。

    未通過檢查的段落回退切點時間戳並標記，其餘段照常——ADR-0004 的兩層降級之
    第一層。

    帶 omission 的段落跳過檢查：adapter 已經說明它為何沒有字，再套一次判準只會得到
    「字級清單為空」這種比原因更空洞的描述。它們的原因按內容分組後合記（見
    `_log_omissions`），故服務整體失敗時不會產生 N 條相同訊息（#36 實測 63 條）而把
    真正的原因推出畫面（#37）。
    """
    last = len(segments) - 1
    merged: list[AlignedSegment] = []
    omitted: dict[Omission, list[int]] = {}
    # strict：筆數不符即 Protocol 被違約。靜默截短會讓尾端 Segment 從回應消失，使
    # segments 與 transcription_only 互相矛盾，比直接失敗更難察覺。
    for index, (segment, alignment) in enumerate(
        zip(segments, alignments, strict=True)
    ):
        if alignment.omission is not None:
            omitted.setdefault(alignment.omission, []).append(index + 1)
            merged.append(_with_alignment(segment, [], aligned=False))
            continue
        defect = _find_segment_defect(
            segment,
            alignment.words,
            span_applies=0 < index < last,
            bounds=alignment.bounds,
        )
        if defect is not None:
            # warning 而非 info：本專案無 logging 設定，info 會被靜默丟棄（見模組
            # docstring）。送出且有回應的段落逐段都記，包含空清單，因為那可能是
            # 「該段全是標點」這種真正需要知道的情形（#34）。
            logger.warning(
                "第 %d 段未通過對齊檢查（%s）：%s", index + 1, defect.code, defect.detail
            )
        merged.append(_with_alignment(segment, alignment.words, aligned=defect is None))
    _log_omissions(omitted)
    return merged, _summarize(merged, audio_duration=audio_duration)


# 合記一條時最多列出幾個段號。夠多到能認出是哪幾段、又不會讓 57 段同因時整行都是數字
# 而把原因本身擠出視線。
_MAX_LISTED_SEGMENTS = 8


def _log_omissions(omitted: dict[Omission, list[int]]) -> None:
    """把 adapter 給的原因分組記下，一組一條。

    分組鍵是整個 Omission，故同一批失敗的段落自動合成一條、不同批的各自留著——服務在
    兩次請求之間被重啟時可能一批逾時、另一批回 503，合成一條會丟掉其中一個。
    """
    for omission, indexes in omitted.items():
        listed = "、".join(str(i) for i in indexes[:_MAX_LISTED_SEGMENTS])
        if len(indexes) > _MAX_LISTED_SEGMENTS:
            listed = f"{listed} 等 {len(indexes)} 段"
        logger.warning(
            "第 %s 段未取得字級結果（%s）：%s", listed, omission.code, omission.detail
        )


def _find_segment_defect(
    segment: Segment,
    words: list[Word],
    *,
    span_applies: bool,
    bounds: tuple[float, float],
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
        words, span=span if span_applies else None, bounds=bounds
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
        too_short = duration < WORD_TIME_RESOLUTION_SECONDS - _DURATION_TOLERANCE_SECONDS
        if too_short or duration > MAX_WORD_SECONDS:
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
