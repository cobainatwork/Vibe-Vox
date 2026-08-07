# Vibe-Vox

自架的 ASR/TTS 測試與管理平台。ASR 由 VibeVoice-ASR 提供、TTS 由 VoxCPM2 提供，前端為單一統一操作頁，涵蓋 Hotwords 管理、ASR 測試、TTS 測試與音色管理。

## Language

### 核心引擎

**ASR**：
語音轉文字。本專案一律指 VibeVoice-ASR，輸出帶語者、時間戳的結構化結果。
_Avoid_: STT、語音辨識引擎（泛稱時）

**TTS**：
文字轉語音。本專案一律指 VoxCPM2。
_Avoid_: 語音合成引擎（泛稱時）

### ASR 領域

**Hotword**：
使用者維護的一個詞彙條目（人名、專有名詞、術語），用來提升 ASR 對特定內容的辨識準確度。本專案的 Hotword 是結構化清單資料，非模型原生的加權關鍵字。
_Avoid_: 熱詞加權、關鍵字權重

**Context prompt**：
辨識時把所有啟用中的 Hotword 編譯成的一段自由文字，透過 VibeVoice-ASR 的 `prompt` 參數注入。這是 Hotword 實際作用於模型的唯一途徑。
_Avoid_: hotword 參數、關鍵字欄位

**Segment**：
ASR 輸出的一個區塊，含 `Start`、`End`、`Speaker`、`Content` 四欄。多個 Segment 構成一次辨識的完整結果（Who/When/What）。

VibeVoice-ASR 的分段是**窮盡連續切分**而非語句切分：模型自選切點把音訊切滿，段長約 30–40 秒，段界與句子邊界無關，相鄰段的原始時間戳幾乎相接。故 Segment 不是「一句話」。

經 Forced alignment 後，Segment 的 `Start`／`End` 改由該段首字與末字的實際發音邊界重算，段間才會出現有意義的間隙。
_Avoid_: 句子、語句單位、片段、utterance（中文語境）

**Word（對齊單位）**：
Forced alignment 產生時間戳的最小單位。**中文為單一漢字，不是語意上的詞**——「保險經紀人」是五個 Word，不是一個。連續的拉丁字母或數字為一個 Word。**標點與符號不產生 Word**，故一段的 Word 數量不等於其 `Content` 的字元數。命名沿用官方語彙，但語意以此定義為準。
_Avoid_: 詞、詞彙、斷詞結果（本專案不做中文斷詞）

**Forced alignment（強制對齊）**：
把已知的轉錄文字對回音訊、求出每個 Word 實際發音起訖時刻的動作。與 ASR 是兩件事：ASR 決定「說了什麼」，Forced alignment 只決定「哪個字落在哪一刻」，不更動文字內容。
_Avoid_: 時間戳校正、對時、同步

**對齊狀態（Alignment status）**：
單一 Segment 的字級時間戳是否可信的顯式標記。強制對齊無容錯機制，轉錄文字有誤時會靜默對歪，故每段須明示對齊成功與否；未通過合理性檢查者回退為切點時間戳並標記，不以「Word 清單為空」隱含表示。
_Avoid_: 對齊失敗旗標（僅描述其中一種值）

**一次辨識（Transcription）**：
一份音檔從輸入到產出「帶字級時間戳的 Segment 清單 + 四個彙總數字」的完整過程，以及該過程的產出。四個步驟——音檔輸入與轉碼、ASR、Forced alignment、合理性檢查——其順序與時序約束屬於同一個概念，不可分屬不同層：**音檔的實際長度必須在暫存檔被回收前取得，而彙總發生在回收之後**。

不是 Turn：Turn 是消費端的一次完整發話，而管理平面辨識的是任意音檔（實測情境含 10 分鐘、63 段的會議錄音）。
_Avoid_: 辨識流程（過程與產出是同一個詞）、ASR 結果（那只是第一步的產出，見 `AsrResult`）

**對齊缺漏（Omission）**：
某個 Segment 為何拿不到字級時間戳的原因，由對齊實作回報。與對齊狀態是不同的問題：對齊狀態答「這段的時間戳可不可信」，對齊缺漏答「這段為什麼根本沒有時間戳」——未送出（非語音標記段、文字為空、切片為零長度）、該批失敗、整份逾時預算用盡。

**沒有缺漏但 Word 清單為空是合法狀態**，代表對齊服務確實回了零個字（例如整段都是標點），該交由合理性檢查處理而非當成故障。相同的缺漏在記錄時合為一條，故服務整體失效不會產生 N 條同質訊息。
_Avoid_: 對齊錯誤（多數缺漏不是錯誤）、降級原因（降級是結果不是原因）

### TTS 音色領域

**Voice（音色）**：
一個可被 TTS 選用的發聲身分。分兩型：Voice clone、Voice design。是「可選音色」下拉選單的統一概念。每個 Voice 都由人建立，系統不附任何音色。
_Avoid_: speaker（泛稱時）、聲線、tone

**Voice clone**：
上傳參考音檔建立的音色。
_Avoid_: 複製聲音、仿聲

**Voice design**：
使用者以文字描述建立的音色。建立時定版（擷取首次輸出為參考音），之後走 clone 路徑重播以確保可重現。
_Avoid_: 設計聲音、生成音色

**Instruction**：
控制發聲方式的自然語言指示，以聲學特徵（音量、語速、句尾走向）表述而非情緒名稱。兩型 Voice 皆生效。
_Avoid_: prompt（此詞在 ASR 已另有所指）、風格描述、情緒指令（實測情緒名稱無效，見 `docs/api/tts.md`）

**Utterance（合成單位）**：
一次合成呼叫承載的一句文字與其 Instruction。是 TTS 側的單位，**與 ASR 的 Segment、消費端的 Turn 都無關**——那兩者的 `_Avoid_` 都列了 utterance，指的是「不要用 utterance 稱呼它們」，不是禁止本詞條。分成獨立單位的理由是模型限制：一次呼叫只承載一種 Instruction，要逐句換語氣就得逐句呼叫。
_Avoid_: 句子（切句規則不在此層）、語句、片段

**定版（Pinning）**：
建立 Voice design 時執行一次生成並擷取輸出音檔存為參考音的動作，使該音色之後可穩定重現。
_Avoid_: 快照、固定

### 系統

**能力感知（Capability-aware）**：
介面只提供模型實際具備的能力，不讓使用者對不存在的能力下指令的設計原則。
_Avoid_: 智慧切換、動態表單

**BFF**：
薄應用後端（FastAPI），負責前端服務、持久化、上傳轉檔、Hotword 編譯、Voice 對應與呼叫模型端點。本身不載入模型。
_Avoid_: API server（泛稱）、gateway

### 消費端整合

**消費端資料平面**：
AI_practise 以 REST 契約呼叫 ASR/TTS 的介面面（`/api/asr/transcribe`、`/api/tts/*`、`/api/hotwords`）。形狀為約束性，見 ADR-0003。
_Avoid_: 對外 API（泛稱）

**管理平面**：
操作者設定與測試同一後端的介面面（Hotword 管理、ASR/TTS 測試、音色管理）。功能為消費端資料平面的超集。
_Avoid_: 後台、admin panel（泛稱）

**Turn（回合）**：
學員一次完整發話。ASR 以回合為單位做批次辨識，不做邊講邊出的即時 partial。
_Avoid_: 片段、utterance
