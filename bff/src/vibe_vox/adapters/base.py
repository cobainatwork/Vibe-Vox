"""AsrClient/TtsClient/AlignerClient 介面（ADR-0001 的唯一 stub 邊界）。

#1 walking skeleton 僅需就緒探測 health()。辨識 transcribe()（#5）與合成
synthesize()（其對應票）隨各票加入本介面。字級對齊 align()（#27）為 ADR-0004
新增的第三個邊界，其實作為獨立部署單元而非模型 in-process 載入。
"""

from dataclasses import dataclass
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


class AsrResult(BaseModel):
    """ASR 模型端回的辨識結果，鏈路的第一手材料。

    **不含 duration**：那是所有 Segment 的 End 最大值，而段界經 Forced alignment 後會
    重算為末字的 End，故該值只有在對齊之後才確定，屬 `transcription.Transcription`。
    """

    segments: list[Segment]
    raw_text: str
    transcription_only: str


class AsrUnavailable(Exception):
    """ASR 模型服務連不上、回錯或回傳信封異常（`AsrClient` 的錯誤模式，映射 → 502）。"""


class AsrTimeout(Exception):
    """ASR 模型服務呼叫逾時（`AsrClient` 的錯誤模式，映射 → 504）。"""


@runtime_checkable
class AsrClient(Protocol):
    async def health(self) -> bool:
        """回報 ASR 模型服務是否就緒。"""
        ...

    async def transcribe(self, audio: Path, *, context: str) -> AsrResult:
        """辨識正規化後的 wav，回帶語者與時間戳的分段結果。

        錯誤模式為 `AsrUnavailable` 與 `AsrTimeout`，實作須把自己的傳輸細節翻譯成
        這兩者。逐字稿是這條路徑的產出本身而非附加功能，故兩者都往上映射成狀態碼
        （502／504），不像對齊那樣就地降級。
        """
        ...


@dataclass(frozen=True)
class Omission:
    """一段沒有字級結果的原因。

    形狀比照 `alignment.Defect`：code 為穩定的機器可讀值，detail 給人看並含具體的數值
    與服務端訊息。分成兩欄而非一句格式化文字，是因為**整個值就是下游的分組鍵**——同一
    批失敗的段落得到相等的 Omission 而合記一條，跨批的 detail 不同故各自留著（跨批不
    保證同因）。若只有一句文字，分組會依賴「訊息剛好逐字相同」這種巧合，而任何隨段落
    變動的細節都會悄悄破壞它。
    """

    code: str
    detail: str


@dataclass(frozen=True)
class SegmentAlignment:
    """單一 Segment 的字級對齊結果。

    words 的時間戳為**原音檔的絕對時間**（切片 offset 已加回）。對齊品質的合理性
    檢查與降級標記不在此層，屬 #28。

    bounds 是該段切片在原音檔實際涵蓋的時間範圍，供落界判準使用。**由實作回報而非
    讓呼叫端重算**：夾限規則與 buffer 設定只有實作知道，呼叫端重算等於複製一份並期待
    兩者永不漂移；且夾限取的是 frame 格點，未量化的重算值會與 words 的時間戳落在格點
    兩側，使正常的字被判為落在範圍外。

    words 為空時 bounds 仍有效——未取得字級結果不代表該段沒有音訊。

    omission 說明該段為何沒有字級結果（未送出、該批失敗、服務整體不可用），為 None 時
    表示實作確實取得了結果——**空的 words 配 None 是有意義的組合**，代表服務回了零個
    字，該由合理性檢查處理而非當成故障。**原因隨結果過 seam 而不是只寫進 log**：呼叫端
    據此知道這段的空已被解釋，否則它只能逐段重述「字級清單為空」，把唯一的真原因洗掉
    （#36 實測為 1 條真原因加 63 條同質雜訊）。
    """

    words: list[Word]
    bounds: tuple[float, float]
    omission: Omission | None = None


@runtime_checkable
class AlignerClient(Protocol):
    async def health(self) -> bool:
        """回報對齊服務是否就緒。"""
        ...

    async def align(
        self, audio: Path, segments: list[Segment]
    ) -> list[SegmentAlignment]:
        """逐段對齊，回每段的字級時間戳與其切片範圍，順序與 segments 一一對應。

        **對齊服務不可用或逾時不拋出，回全段的空結果並在 omission 說明原因。** 這是
        ADR-0004 第二層降級的所在：對齊是附加功能，逐字稿有獨立價值，不因它失效而
        一併不可得。放在此層而非由呼叫端攔例外，是因為降級後仍須回報每段的 bounds，
        而那只有實作算得出來——兩者分屬兩層就必然有一層要重算另一層的東西。

        音檔本身讀不到（路徑不存在、非合法 wav）仍會拋出：那不是對齊失效，是呼叫端
        給錯了東西。
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

    **純空白的 instruct 視同沒給**，同屬本型別的不變量而非呼叫端的責任：adapter 會把
    它組成「(   )」前綴，而括號不被剝除，模型會把空前綴當成要處理的內容，而非依契約
    §5.2 退回音色本身的語氣。它與中性化是同一類東西（都在決定「送出去的字串長什麼
    樣」），分屬兩層就會有一層忘記做。

    **保證的範圍以正常建構為限。** `Utterance(...)` 與屬性指派會過 validator；pydantic
    的 `model_construct()` 與 `model_copy(update=...)` 依設計跳過驗證，兩者都繞得過。
    這不是疏漏而是 pydantic 的逃生口，但要知道它在哪：切句實作時最自然的寫法正是
    `model_copy(update={"text": chunk})`，而那條路不會中性化——切句應改為重新建構。
    """

    # frozen 擋住屬性指派那條路（`u.text = "(evil)"`）。另外兩條見上方 docstring：
    # pydantic 的逃生口關不掉，只能寫明。
    model_config = ConfigDict(frozen=True)

    text: str
    instruct: str | None = None

    @field_validator("text")
    @classmethod
    def _neutralize_text(cls, v: str) -> str:
        # strip 與 instruct 那條同理：本型別的職責是「送出去的字串長什麼樣」，頭尾空白
        # 屬於那個問題。端點的 to_speakable 已經 strip 過，但第二個呼叫端（切句）不會。
        return neutralize_control_syntax(v).strip()

    @field_validator("instruct")
    @classmethod
    def _neutralize_instruct(cls, v: str | None) -> str | None:
        if not v:
            return None
        # 中性化後才判空白：順序反過來的話，只含控制語法的 instruct 會留下一個中性化
        # 後為空、卻不是 None 的字串，adapter 照樣組出空前綴。
        return neutralize_control_syntax(v).strip() or None


class TtsUnavailable(Exception):
    """TTS 模型服務連不上、回非 2xx，或回的不是可解析的音訊
    （`TtsClient` 的錯誤模式，映射 → 502）。"""


class TtsTimeout(Exception):
    """TTS 模型服務呼叫逾時（`TtsClient` 的錯誤模式，映射 → 504）。"""


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

        錯誤模式為 `TtsUnavailable` 與 `TtsTimeout`，實作須把自己的傳輸細節翻譯成
        這兩者。翻漏的例外會穿過端點層冒成 500，而 500 不在 docs/api/tts.md 的錯誤表
        內——消費端拿到的是非契約形狀的回應，它的錯誤處理分支涵蓋不到。

        **reference_audio 可讀是呼叫端的前置條件，目前無人保證。** 它指向不存在的檔案
        時各實作行為不一致（一個冒 500、一個靜默回靜音），而 500 不在 docs/api/tts.md
        的錯誤表內。真正的修法是讓可用性在 Voice 建立時成為該音色的不變量，追蹤於 #45
        （時長超界是不同的缺口，見 #44）。
        """
        ...
