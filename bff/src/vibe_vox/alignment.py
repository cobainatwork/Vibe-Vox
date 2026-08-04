"""對齊結果的合理性檢查與合併（ADR-0004 的兩層降級之第一層）。

強制對齊**無容錯機制**：訓練以 MFA pseudo-label，假設轉錄文字與音訊完全對應。
文字含亂碼、漏字或多字時，模型會硬把字塞到某個時間位置而**不報錯**。沒有這層
檢查，對歪的時間戳會以「看似正常」的形式流到評分端。

四項判準中三項為物理界限（由模型參數或音訊範圍導出），只有跨距判準是比例值。可用
的實測樣本只有 #26 那 13 個字，故比例值取得刻意寬鬆且限定適用範圍——誤判的代價是
把本可用的段落標為不可信，使評分端反而拿不到資料。三個推算閾值待實測校準（#32）。
"""

from pydantic import BaseModel

from vibe_vox.adapters.base import Segment, Word

# 單字時長的下界，即模型的時間解析度（config.json 的 timestamp_segment_time，
# 80ms，ADR-0004）。小於一個解析度單位的時長模型無法真實產生，只能是退化輸出；
# #26 實測到的零時長「幾」即落在此界之下。
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
) -> tuple[list[AlignedSegment], AlignmentSummary]:
    """合併對齊結果、逐段檢查、重算段界，並算出四個彙總數字。

    未通過檢查的段落回退切點時間戳並標記，其餘段照常——ADR-0004 的兩層降級之
    第一層。
    """
    last = len(segments) - 1
    merged = [
        _with_alignment(
            segment,
            words,
            aligned=_is_trustworthy(
                segment,
                words,
                span_applies=0 < index < last,
                audio_duration=audio_duration,
                slice_buffer=slice_buffer,
            ),
        )
        # strict：筆數不符即 Protocol 被違約。靜默截短會讓尾端 Segment 從回應消失，
        # 使 segments 與 transcription_only 互相矛盾——比直接失敗更難察覺。
        for index, (segment, words) in enumerate(
            zip(segments, words_per_segment, strict=True)
        )
    ]
    return merged, _summarize(merged, audio_duration=audio_duration)


def _is_trustworthy(
    segment: Segment,
    words: list[Word],
    *,
    span_applies: bool,
    audio_duration: float,
    slice_buffer: float,
) -> bool:
    """該段的對齊結果是否可信。

    零長度段落一律不可信：模型輸出缺欄位時 `Start` 與 `End` 同補 0.0
    （docs/api/asr.md §6），該段的切片只有 buffer 區的音訊卻配整段話的文字，對齊
    結果必然無意義——但每個字的時長、順序與範圍都可能正常，`is_sane` 的前三項判準
    攔不到它。零長度本身即退化訊號。

    **首段與末段不套跨距判準**（`span_applies`）：頭尾沉默是預期情境而非故障——
    開頭沉默（按下錄音後不敢開口）本身即話術缺失、結尾沉默有「講完忘記按停止」與
    「講不下去」兩種語義，兩者都必須保留給消費端判斷（ADR-0004）。它們會壓低首末
    段的跨距比，若套跨距判準就會把正常段標為對齊失敗、丟棄其 words，而那正是消費端
    唯一能算出頭尾沉默的資料。中間段沒有這個問題：窮盡連續切分下，中間段的首字與
    末字之間就是該段的全部內容，段內停頓已被跨距涵蓋，故其比例應接近 1。

    代價是首末段的擠壓型對歪抓不到。單段錄音（既首亦末）則完全不套。記於 #32。
    """
    span = segment.End - segment.Start
    if span <= 0:
        return False
    return is_sane(
        words,
        span=span if span_applies else None,
        bounds=_bounds(
            segment, audio_duration=audio_duration, slice_buffer=slice_buffer
        ),
    )


def is_sane(
    words: list[Word], *, span: float | None, bounds: tuple[float, float]
) -> bool:
    """該段的字級時間戳是否可信。

    span 為該段的切點跨距（`End − Start`），`None` 表示該段不適用跨距判準（首段與
    末段，理由見 `merge_alignment`）。bounds 為該段切片在原音檔的時間範圍。

    空清單回 False：無從驗證即不可信，且下游無法據以重算段界。
    """
    if not words:
        return False

    lower, upper = bounds
    previous_end = None
    for word in words:
        duration = word.End - word.Start
        if duration < MIN_WORD_SECONDS or duration > MAX_WORD_SECONDS:
            return False  # 單字時長異常
        if previous_end is not None and word.Start < previous_end:
            return False  # 時間戳順序逆轉
        if word.Start < lower or word.End > upper:
            return False  # 落在該段音訊涵蓋的範圍外
        previous_end = word.End

    if span is not None and words[-1].End - words[0].Start < span * MIN_SPAN_RATIO:
        return False  # 對齊跨距遠短於該段音訊長度
    return True


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
