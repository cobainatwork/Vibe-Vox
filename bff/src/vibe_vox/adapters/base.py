"""AsrClient/TtsClient/AlignerClient 介面（ADR-0001 的唯一 stub 邊界）。

#1 walking skeleton 僅需就緒探測 health()。辨識 transcribe()（#5）與合成
synthesize()（其對應票）隨各票加入本介面。字級對齊 align()（#27）為 ADR-0004
新增的第三個邊界，其實作為獨立部署單元而非模型 in-process 載入。
"""

from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, field_validator

from vibe_vox.audio.wav import PcmAudio, PcmSpec
from vibe_vox.tts_text import neutralize_control_syntax


class Segment(BaseModel):
    """ASR 輸出的一個區塊（Who/When/What），欄位形狀為消費端契約約束。

    非語句單位：VibeVoice-ASR 為窮盡連續切分，段界是模型自選的切點而非語音
    邊界，相鄰段的 End 與下一段 Start 幾乎總是相同。詳見 CONTEXT.md 的
    Segment 詞條與 docs/api/asr.md §4.3。
    """

    Start: float
    End: float
    Speaker: str
    Content: str


class Word(BaseModel):
    """Forced alignment 產生時間戳的最小單位，秒（CONTEXT.md 的 Word 詞條）。

    中文為單一漢字而非語意上的詞，且標點與符號不產生 Word，故一段的 Word 數量
    不等於其 Content 的字元數。欄位大寫以對齊 Segment 與消費端契約
    （docs/api/asr.md §4.4）。
    """

    Text: str
    Start: float
    End: float


class TranscriptionResult(BaseModel):
    """一次辨識的完整結果。applied_context 由端點層另行附加，不屬本結果。"""

    segments: list[Segment]
    raw_text: str
    transcription_only: str
    duration: float


@runtime_checkable
class AsrClient(Protocol):
    async def health(self) -> bool:
        """回報 ASR 模型服務是否就緒。"""
        ...

    async def transcribe(self, audio: Path, *, context: str) -> TranscriptionResult:
        """辨識正規化後的 wav，回帶語者與時間戳的分段結果。"""
        ...


@runtime_checkable
class AlignerClient(Protocol):
    async def health(self) -> bool:
        """回報對齊服務是否就緒。"""
        ...

    async def align(
        self, audio: Path, segments: list[Segment]
    ) -> list[list[Word]]:
        """逐段對齊，回每段的字級時間戳，順序與 segments 一一對應。

        時間戳為**原音檔的絕對時間**（切片 offset 已加回）。對齊品質的合理性
        檢查與降級標記不在此層，屬 #28。
        """
        ...


# 合成輸出的規格，消費端契約的一部分（ADR-0003、docs/api/tts.md §5.3），不因引擎更換
# 而改變。放在介面層而非某個 adapter：每個實作都要落在這個規格上，stub 也不例外。
CONTRACT_SPEC = PcmSpec(sample_rate=24000)


class Utterance(BaseModel):
    """一句待合成的文字與其發聲方式。

    instruct 描述發聲方式（音量、語速、句尾走向）而非情緒名稱——實測情緒標籤在
    輸出上量不到差異，見 docs/superpowers/specs/2026-08-05-voxcpm2-style-control-measured.md。

    一句一個 Utterance 是因為模型一次呼叫只承載一種風格。切句在此介面之上完成，
    中文斷句規則不進 adapter。

    **控制語法的中性化由本型別保證，不是呼叫端的責任。** instruct 由 adapter 組成行內
    `(...)` 前綴併入同一個字串，故未中性化的括號能讓使用者文字變成風格指令，或讓
    instruct 跳出自己的前綴。靠呼叫端記得做的話，第二個呼叫端就是漏洞。
    """

    # frozen + validate_assignment 讓上面那句「由本型別保證」真的成立：沒有它，
    # `u.text = "(evil)"`、`model_construct()` 與 `model_copy(update=...)` 三條路都
    # 繞過 validator，而 docstring 宣稱的保證範圍會比實際大。
    model_config = ConfigDict(frozen=True, validate_assignment=True)

    text: str
    instruct: str | None = None

    @field_validator("text", "instruct")
    @classmethod
    def _neutralize(cls, v: str | None) -> str | None:
        return neutralize_control_syntax(v) if v else v


@runtime_checkable
class TtsClient(Protocol):
    async def health(self) -> bool:
        """回報 TTS 模型服務是否就緒。"""
        ...

    async def synthesize(
        self, utterances: list[Utterance], *, reference_audio: Path
    ) -> PcmAudio:
        """逐句合成後串接，回契約規格（CONTRACT_SPEC）的 PCM。

        **回 PcmAudio 而非 wav bytes。** 容器化屬端點層——`response_format` 決定要包
        wav 還是直出裸 PCM，而 adapter 若先包好 wav，pcm 路徑就得把剛寫上的標頭再拆
        一次。回帶規格的 PCM 也讓「這段音訊符不符合契約」是一次比較而非三個欄位各比。

        **串接在 PCM 層做。** 直接把每句的 wav bytes 相接會在每個句界埋進 44 bytes 的
        RIFF 標頭：消費端在句與句之間聽到爆音，而標頭的長度欄位只描述第一句，嚴格的
        解碼器會就此截斷。

        **不插入句間靜音。** 停頓由文字層的標點決定，是模型的事；在此補靜音會讓同一段
        話經一次請求與經多次請求聽起來不同。

        reference_audio 是音色身分的唯一錨點（ADR-0002 的定版產物或使用者上傳的
        參考音）。實作**不得**送出該參考音的逐字稿：送了會讓 VoxCPM2 落到 Hi-Fi
        模式並靜默忽略 instruct（docs/api/tts.md §5.2）。
        """
        ...
