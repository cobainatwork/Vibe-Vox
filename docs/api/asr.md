# ASR API 規格：`POST /api/asr/transcribe`

消費端資料平面的語音辨識端點。回合制批次辨識——送一段完整音檔、拿一份完整結果，不做邊講邊出的即時 partial。

本文所述皆為**已實作並經測試涵蓋**的行為。字級強制對齊（ADR-0004）於 #28 落地，原標記為〔規劃〕的欄位現已生效。

---

## 1. 端點

```
POST /api/asr/transcribe
Content-Type: multipart/form-data
```

正式部署對外經 nginx 的 `8088` 埠，路徑 `/api/` 反向代理至 BFF。

---

## 2. 存取控制

**server-to-server 呼叫不需任何標頭，直接放行。**

BFF 有一層 Origin 防護（`OriginGuardMiddleware`），規則是「來源存在且不在白名單才拒」。判定順序：先看 `Origin` 標頭，無則退而解析 `Referer`，兩者皆無則視為無來源、**放行**。

這是刻意的設計：瀏覽器對跨來源的不安全請求一定會帶 `Origin`，而 server-to-server 的 HTTP client（AI_practise 的呼叫路徑）通常不帶。因此該防護擋得住第三方網頁的跨站偽造，不會擋到你們。

若你們的 HTTP client 會自動附加 `Origin`（少數框架有此行為），需滿足下列其一，否則回 403：

- 該來源列入 `VIBE_VOX_FRONTEND_ORIGINS` 白名單（環境變數，逗號分隔）
- 或 `Origin` 的 host:port 與請求的 `Host` 標頭相同（同源）

`GET`／`HEAD`／`OPTIONS` 不受此限。

---

## 3. 請求

### 3.1 表單欄位

| 欄位 | 型別 | 必填 | 預設 | 說明 |
|---|---|---|---|---|
| `file` | 檔案 | 是 | — | 音檔本體 |
| `extra_terms` | 字串 | 否 | `"[]"` | 本次臨時 Hotword，**JSON 字串陣列**，例如 `["安聯人壽","變額萬能壽險"]` |
| `replace_context` | 布林 | 否 | `false` | `true` 時只用 `extra_terms`，忽略資料庫中所有啟用的 Hotword |

`extra_terms` 必須是合法 JSON 且解析後為字串陣列，否則回 400 `INVALID_EXTRA_TERMS`。陣列元素會經清洗，清洗後為空的項目自動剔除。

`replace_context` 的用途是讓單次辨識完全掌控 context，不受管理平面既有設定干擾——適合 A/B 測試或針對特定話術腳本的辨識。

### 3.2 音檔格式

容器型別由**檔頭 magic number 判定，不信任副檔名**。允許：

| 容器 | 判定依據 |
|---|---|
| wav | `RIFF` + offset 8 為 `WAVE` |
| mp3 | `ID3` 標籤，或 MPEG frame sync（裸幀） |
| flac | `fLaC` |
| ogg | `OggS` |
| m4a / mp4 | offset 4 為 `ftyp` |
| webm / mkv | EBML `1A 45 DF A3` |

不符者回 400 `UNSUPPORTED_AUDIO_FORMAT`。此為廉價前置閘，真正的解碼驗證由 ffmpeg 執行——通過 sniff 但無法解碼者回 400 `TRANSCODE_ERROR`。

所有輸入一律轉碼為 **24 kHz 單聲道 wav** 後才送入模型。取樣率由 `VIBE_VOX_ASR_SAMPLE_RATE` 控制，預設 24000，對齊官方 vLLM plugin 的目標取樣率——plugin 內部一律 resample 至 24 kHz，故先降至更低取樣率只會丟失無法還原的高頻。

### 3.3 大小與長度上限

| 限制 | 值 | 超過時 |
|---|---|---|
| nginx 請求體 | 210 MB | **nginx 直接回 413，非本 API 的 JSON 錯誤信封** |
| BFF 音檔上限 | 200 MB（`209715200` bytes） | 400 系列 JSON：413 `FILE_TOO_LARGE` |
| 模型音訊長度 | 61 分鐘（vLLM plugin 的 `VIBEVOICE_MAX_AUDIO_DURATION`，預設 3660 秒） | 502 `ASR_UNAVAILABLE` |

**注意第一列**：超過 210 MB 的請求在到達 BFF 前就被 nginx 擋下，你們收到的會是 nginx 的預設錯誤頁（HTML）而不是 `{"error":{...}}`。解析回應時需容忍這個例外——建議以 `Content-Type` 判斷再決定是否解析 JSON。

---

## 4. 回應

### 4.1 信封規則（注意不對稱）

**成功回應不套信封**，欄位直接位於根層（ADR-0003：消費端資料平面不使用管理平面的 `{data}` 包裝）。

**錯誤回應一律套信封**：`{"error": {"code": "...", "message": "..."}}`。

### 4.2 成功回應：核心欄位

```jsonc
{
  "segments": [
    {
      "Start": 0.0,
      "End": 39.57,
      "Speaker": "0",
      "Content": "王安蓮您好，我是服務於好棒棒保險經紀人股份有限公司的王大明。"
    }
  ],
  "raw_text": "...",              // 模型原始輸出字串，未經繁體轉換，供除錯
  "transcription_only": "...",    // 所有 Segment 的 Content 串接，無時間與語者
  "duration": 214.68,             // 所有 Segment 的 End 最大值
  "applied_context": "..."        // 本次實際注入的 Hotword context，頓號連接
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| `segments[].Start` / `.End` | float，秒 | 見下方 4.3 的重要警告 |
| `segments[].Speaker` | string | 語者標識。模型輸出數字時會是 `"0"`、`"1"` 的字串形式，非整數 |
| `segments[].Content` | string | 該段文字，已轉台灣繁體（OpenCC `s2tw`，純字形轉換、不改詞彙） |
| `raw_text` | string | 模型原始輸出，**保留簡體**。用於比對繁化前後差異、排查解析問題 |
| `transcription_only` | string | 純逐字稿。模型輸出非 JSON 時，此欄為繁化後的原始文字，`segments` 為空陣列 |
| `duration` | float，秒 | 所有 Segment 的 `End` 最大值。`segments` 為空時為 `0.0`。**這不是音檔實際長度**——尾端靜音不計入，故恆小於或等於實際長度。定義不變，但段界已隨對齊重算，故其值隨 `aligned` 改變；要音檔實際長度請用 `alignment.audio_duration` |
| `applied_context` | string | 實際注入的 context，**以頓號（`、`）連接各 Hotword**。用於確認 Hotword 是否如預期生效。未套用任何 Hotword 時為空字串 |

上表之外，回應**恆含**字級對齊的三個欄位（`segments[].aligned`、`segments[].words`、根層 `alignment`），見 §4.4。

### 4.3 時間戳語義的重要警告

**`Segment.Start`／`End` 的語義隨 `aligned` 改變。** `aligned` 為 `true` 時是首字與末字的實際發音邊界；為 `false` 時退回下述的切點語義。**混用會得到錯誤結果。**

切點語義是這樣的：VibeVoice-ASR 的分段為窮盡連續切分——第一段自 `0.0` 起、模型把整段音訊切滿、段長約 30–40 秒，且**相鄰段的 `End` 與下一段 `Start` 幾乎總是完全相同**。段界與句子邊界無關。

因此對 `aligned: false` 的段落：

- 不可用 `Segment.End − Segment.Start` 推斷「這句話講了多久」
- 不可用段間間隙推斷停頓——該值恆為 0
- `Segment` 不是「一句話」

`aligned: true` 的段落沒有這些限制：段界即實際發音邊界，段間間隙即句間停頓。

此性質已在 `CONTEXT.md` 的 Segment 詞條記載。

### 4.4 成功回應：字級對齊（ADR-0004）

下列欄位恆存在於回應中。既有欄位形狀不變（向後相容）。

```jsonc
{
  "segments": [
    {
      "Start": 0.42,              // 改為該段首字的實際發音起點
      "End": 38.91,               // 改為該段末字的實際發音終點
      "Speaker": "0",
      "Content": "王安蓮您好…",
      "aligned": true,            // 對齊狀態，顯式標記
      "words": [
        { "Text": "王", "Start": 0.42, "End": 0.58 },
        { "Text": "安", "Start": 0.58, "End": 0.71 }
      ]
    }
  ],
  "alignment": {
    "audio_duration": 218.30,     // 音檔實際總長
    "speech_start": 0.42,         // 首字 Start；此值本身即開頭沉默時長
    "speech_end": 214.02,         // 末字 End；audio_duration 減之即結尾沉默時長
    "aligned_duration": 190.55    // 所有 aligned=true 段落的總時長
  }
}
```

| 欄位 | 說明 |
|---|---|
| `segments[].words[]` | 字級時間戳。**中文為單一漢字，不是詞**——「保險經紀人」是五個元素。本系統不做中文斷詞 |
| `segments[].aligned` | 該段字級時間戳是否可信。`false` 時 `words` 為空陣列，且 `Start`／`End` 退回切點語義 |
| `alignment.audio_duration` | 音檔實際總長。**唯一不受對齊狀態影響的彙總值** |
| `alignment.speech_start`／`speech_end` | **僅計 `aligned: true` 的段落**，見下方警告。無任何段落對齊成功時為 `null` |
| `alignment.aligned_duration` | 所有 `aligned: true` 段落的時長之和（各段 `End − Start` 相加，不含段間間隙） |

**`aligned: true` 不保證每個字的時間戳都精確。** 它保證的是該段的對齊**結構**可信（順序單調、落在音訊範圍內、跨距合理）。個別字的時長仍可能異常，包括零時長，而那是模型的常態輸出（實測異常率約 8%）。對它零容忍會讓 100 字以上的段落幾乎必然被判失敗，代價是整段的時間戳全部拿不到，故目前容許三成以內的字時長異常，超過即判定為系統性對歪並標 `aligned: false`。

實務影響：算停頓時偶爾會遇到零時長的字，或某兩字之間出現不該有的短間隙。**停頓佔全文的百分比是統計量，個別異常值的影響可忽略**；但若你們的評分邏輯會取最大停頓值而非百分比，需要自行過濾離群值。

**`aligned` 必須顯式檢查，不可用 `words.length === 0` 代替判斷**——空陣列與「該段沒有字」語義不同。

**`Segment.Start`／`End` 在 `aligned` 為 `true` 與 `false` 時語義不同**：前者是實際發音邊界，後者退回切點。混用會得到錯誤結果。

**`speech_start`／`speech_end` 只看對齊成功的段落。** 未對齊段沒有字級時間戳，其 `Start`／`End` 是切點——而第一段的切點恆為 `0.0`，把它算進去會使 `speech_start` 變成 0，等於宣稱「沒有開頭沉默」。相較之下，跳過未對齊段至少不會產生假資訊。

**代價是：首段或末段未對齊時，這兩個值會高估頭尾沉默，且沒有訊號告訴你發生了。** 跳過未對齊的首段使 `speech_start` 取到更晚的段落，而開頭沉默就是 `speech_start` 本身，故值偏大；末段同理使 `audio_duration − speech_end` 偏大。若頭尾沉默對你們的評分重要，請自行檢查 `segments[0].aligned` 與 `segments[-1].aligned`。

### 4.5 給評分端的資料使用說明

本系統**不判定停頓、不定義閾值、不預先排除任何區間**。閾值與評分規則屬 AI_practise 的業務邏輯（ADR-0004 決策）。

停頓時長的計算方式：相鄰兩個 Word 的 `後者.Start − 前者.End`。跨 Segment 時同理——前一段最後一個 Word 的 `End` 到後一段第一個 Word 的 `Start`，該值即句間停頓。

分母的選擇由你們決定，四個彙總數字支援各種組法：

| 想計算 | 用 |
|---|---|
| 全程（含頭尾沉默） | `audio_duration` |
| 排除結尾沉默 | `speech_end` |
| 僅計對齊可信的部分 | `aligned_duration` |
| 開頭沉默時長 | `speech_start` 本身 |
| 結尾沉默時長 | `audio_duration − speech_end` |

後四列都用到 `speech_start`／`speech_end`，而**那兩個值只計對齊成功的段落**（§4.4 末段的警告）。首段或末段未對齊時，頭尾沉默會被**高估**。

三點提醒：

**開頭沉默通常該計入。** 按下錄音後遲遲不開口本身即話術缺失，排除它等於允許學員先發呆再開始而不受懲罰。

**結尾沉默語義歧義，本系統無法區分。** 「講完了忘記按停止」與「講不下去、忘詞」在音訊上完全相同，ASR 端沒有任何依據可分辨。該規則需由你們依其他訊號（例如話術腳本是否走完）決定。

**若採用 `aligned_duration` 以外的分母，需自行排除 `aligned: false` 的段落**，否則那些段落會被當成「完全沒有停頓」而使流暢度分數虛高。

---

## 5. 錯誤

| HTTP | `code` | 觸發條件 |
|---|---|---|
| 400 | `VALIDATION_ERROR` | 表單欄位驗證失敗（缺 `file`、型別不符） |
| 400 | `INVALID_EXTRA_TERMS` | `extra_terms` 非合法 JSON 字串陣列 |
| 400 | `UNSUPPORTED_AUDIO_FORMAT` | 檔頭 magic number 不在允許容器清單 |
| 400 | `TRANSCODE_ERROR` | 通過 sniff 但 ffmpeg 無法解碼 |
| 403 | `ORIGIN_FORBIDDEN` | 帶 `Origin`／`Referer` 且非白名單、非同源。server-to-server 不會遇到 |
| 413 | `FILE_TOO_LARGE` | 音檔超過 200 MB |
| 413 | `CONTEXT_BUDGET_EXCEEDED` | Hotword context 估算 token 超過預算（預設 8000）。`message` 含實際值與上限 |
| 502 | `ASR_UNAVAILABLE` | 模型服務連不上、回非 2xx、或回應信封異常（`choices` 空、`content` 為 null） |
| 503 | `TOO_MANY_REQUESTS` | 重量級請求達併發上限（預設 8）。**不排隊，直接 load-shed**，可重試 |
| 504 | `ASR_TIMEOUT` | 模型服務回應逾時（預設 120 秒，`VIBE_VOX_ASR_TIMEOUT_SECONDS`） |
| 504 | `TRANSCODE_TIMEOUT` | ffmpeg 轉碼逾時（預設 60 秒） |
| 504 | `REQUEST_TIMEOUT` | 總體護欄逾時（涵蓋轉碼＋辨識，上限為兩者之和） |

錯誤形狀一律為：

```json
{ "error": { "code": "ASR_TIMEOUT", "message": "語音辨識服務回應逾時。" } }
```

例外：超過 nginx `client_max_body_size`（210 MB）的請求由 nginx 直接回 413 HTML，不經 BFF。

### 重試建議

| `code` | 可重試 |
|---|---|
| `TOO_MANY_REQUESTS` | 是，退避後重試 |
| `ASR_TIMEOUT`／`REQUEST_TIMEOUT` | 是，但先確認音檔長度是否逼近逾時上限 |
| `ASR_UNAVAILABLE` | 是，模型服務可能重啟中 |
| 其餘 400／403／413 | 否，請求本身有問題 |

---

## 6. 邊界情況

| 情況 | 行為 |
|---|---|
| 音訊有效但完全無語音 | `segments` 為空陣列，`duration` 為 `0.0`，HTTP 200。`alignment` 欄位結構完整回傳（`audio_duration` 為音檔實際長度，`speech_start`／`speech_end` 為 `null`，`aligned_duration` 為 `0`），**不報錯** |
| 對齊服務不可用或逾時 | **HTTP 200**，逐字稿照常回傳，全段 `aligned: false`、`words` 為空、`Start`／`End` 維持切點語義。不回 502／504——逐字稿有獨立價值，不因評分這項附加功能失效而一併不可得 |
| 單段對齊未通過合理性檢查 | 該段 `aligned: false`、`words` 為空、`Start`／`End` 退回切點，**其餘段照常**。攔下的是**結構性**問題：時間戳逆轉、落在該段音訊範圍外、對齊跨距不足該段長度的一半（此項不套用於首段與末段，因為頭尾沉默會壓低跨距，那是預期情境而非故障）、零長度段落、字級清單為空 |
| 模型輸出非 JSON | `segments` 為空，`transcription_only` 為繁化後的原始文字，`raw_text` 保留原樣，HTTP 200 |
| 模型輸出缺欄位 | 缺 `Start`／`End` 補 `0.0`，缺 `Speaker`／`Content` 補空字串，不報錯 |
| 模型改用 `Start time` 等鍵名 | 自動相容（三重 fallback：`Start`／`start`／`Start time`） |
| `extra_terms` 為 `"[]"` 或未提供 | 使用資料庫中所有啟用的 Hotword |
| `replace_context=true` 且 `extra_terms` 為空 | context 為空字串，等同不注入 Hotword |

**評分端須處理「完全無語音」**：此時分母為 0，應視為零分或無效作答，而非讓計算炸開或得出 100% 流暢。

---

## 7. 範例

### 請求

```bash
curl -X POST http://10.2.66.102:8088/api/asr/transcribe \
  -F "file=@recording.mp3" \
  -F 'extra_terms=["安聯人壽","變額萬能壽險","躉繳"]' \
  -F "replace_context=false"
```

### 成功回應

刻意示範兩種狀態：第一段對齊成功，第二段未通過合理性檢查。

```jsonc
{
  "segments": [
    {
      "Start": 0.42,              // 首字實際發音起點
      "End": 38.91,               // 末字實際發音終點
      "Speaker": "0",
      "Content": "王安蓮您好，我是服務於好棒棒保險經紀人股份有限公司的王大明。",
      "aligned": true,
      "words": [
        { "Text": "王", "Start": 0.42, "End": 0.58 },
        { "Text": "安", "Start": 0.58, "End": 0.71 }
        // …其餘各字。注意標點不產生 Word，故數量少於 Content 的字元數
      ]
    },
    {
      "Start": 39.57,             // 未通過檢查，退回切點語義
      "End": 76.16,
      "Speaker": "0",
      "Content": "先已完成並瞭解風險屬性評估結果及確認本商品滿足需求。",
      "aligned": false,
      "words": []
    }
  ],
  "raw_text": "[{\"Start\":0.0,\"End\":39.57,\"Speaker\":0,\"Content\":\"王安莲您好…\"}]",
  "transcription_only": "王安蓮您好，我是服務於好棒棒保險經紀人股份有限公司的王大明。先已完成並瞭解風險屬性評估結果及確認本商品滿足需求。",
  "duration": 76.16,
  "applied_context": "安聯人壽、變額萬能壽險、躉繳",
  "alignment": {
    "audio_duration": 78.30,      // 音檔實際總長
    "speech_start": 0.42,
    "speech_end": 38.91,          // 僅計 aligned 的段落
    "aligned_duration": 38.49
  }
}
```

三處值得注意：

第二段的 `39.57` 與第一段原切點相同——這即 §4.3 描述的切點語義，`aligned: false` 時仍是它。

`speech_end` 為 `38.91` 而非 `76.16`：第二段未對齊，其時間戳是切點而非發音邊界，混入彙總會使該值失去意義。若採 `aligned_duration` 以外的分母，**須自行排除 `aligned: false` 的段落**。

`raw_text` 保留模型原始輸出（含簡體與原切點），不隨對齊改變。

### 錯誤回應

```json
{
  "error": {
    "code": "CONTEXT_BUDGET_EXCEEDED",
    "message": "context 估算 9412 tokens 超過上限 8000，請停用部分 Hotword。"
  }
}
```

---

## 8. 相關文件

- ADR-0003：消費端 REST 契約的決策與取捨
- ADR-0004：字級強制對齊的決策，含 §4.4 各欄位與合理性檢查的完整理由
- `CONTEXT.md`：Segment、Word、Forced alignment、對齊狀態的詞彙定義
- `docs/asr-testing.md`：本機與遠端的測試環境架設
