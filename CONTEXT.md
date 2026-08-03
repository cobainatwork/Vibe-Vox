# Vibe-Vox

自架的 ASR/TTS 測試與管理平台。ASR 由 VibeVoice-ASR 提供、TTS 由 Qwen3-TTS 提供，前端為單一統一操作頁，涵蓋 Hotwords 管理、ASR 測試、TTS 測試與音色管理。

## Language

### 核心引擎

**ASR**：
語音轉文字。本專案一律指 VibeVoice-ASR，輸出帶語者、時間戳的結構化結果。
_Avoid_: STT、語音辨識引擎（泛稱時）

**TTS**：
文字轉語音。本專案一律指 Qwen3-TTS。
_Avoid_: 語音合成引擎（泛稱時）

### ASR 領域

**Hotword**：
使用者維護的一個詞彙條目（人名、專有名詞、術語），用來提升 ASR 對特定內容的辨識準確度。本專案的 Hotword 是結構化清單資料，非模型原生的加權關鍵字。
_Avoid_: 熱詞加權、關鍵字權重

**Context prompt**：
辨識時把所有啟用中的 Hotword 編譯成的一段自由文字，透過 VibeVoice-ASR 的 `prompt` 參數注入。這是 Hotword 實際作用於模型的唯一途徑。
_Avoid_: hotword 參數、關鍵字欄位

**Segment**：
ASR 輸出的一個語句單位，含 `Start`、`End`、`Speaker`、`Content` 四欄。多個 Segment 構成一次辨識的完整結果（Who/When/What）。
_Avoid_: 句子、片段、utterance（中文語境）

### TTS 音色領域

**Voice（音色）**：
一個可被 TTS 選用的發聲身分。分三型：Preset speaker、Voice clone、Voice design。是「可選音色」下拉選單的統一概念。
_Avoid_: speaker（泛稱時）、聲線、tone

**Preset speaker**：
Qwen3-TTS 內建的 9 個唯讀語者。可搭配 Instruction。
_Avoid_: 內建聲音、預設 voice（易與「預設值」混淆）

**Voice clone**：
使用者上傳參考音檔加逐字稿建立的音色。不支援 Instruction。
_Avoid_: 複製聲音、仿聲

**Voice design**：
使用者以文字描述建立的音色。建立時定版（擷取首次輸出為參考音），之後走 clone 路徑重播以確保可重現。
_Avoid_: 設計聲音、生成音色

**Instruction**：
控制語氣、情緒、韻律的自然語言指示，對應模型的 `instruct` 參數。僅 Preset speaker 與 Voice design 生效，Voice clone 無效。
_Avoid_: prompt（此詞在 ASR 已另有所指）、風格描述

**定版（Pinning）**：
建立 Voice design 時執行一次生成並擷取輸出音檔存為參考音的動作，使該音色之後可穩定重現。
_Avoid_: 快照、固定

### 系統

**能力感知（Capability-aware）**：
UI 依所選 Voice 型別動態啟用或停用 Instruction 欄位，誠實反映模型能力邊界的設計原則。
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
