# TTS API 規格：`/api/tts/*`

消費端資料平面的語音合成端點。送一段文字與一個音色、拿一段音訊；支援分塊串流以降低首音延遲。

**本文所述端點尚未實作。** 這是供 AI_practise 撰寫 provider 的契約規格，實作與本文同步。已實作的只有 `GET /api/health`（含 TTS 就緒狀態）。ASR 端點見 `asr.md`，其所述為已實作行為。

引擎為 VoxCPM2，經 vLLM-Omni 提供。引擎細節不外露於本契約——除了本文明列的行為外，消費端不應對引擎做任何假設。

---

## 1. 端點總覽

| 方法 | 路徑 | 用途 |
|---|---|---|
| `GET` | `/api/tts/models` | 列出可用模型識別字串 |
| `GET` | `/api/tts/voices` | 列出可用音色 |
| `POST` | `/api/tts/speech` | 合成語音 |
| `GET` | `/api/health` | 含 TTS 服務就緒狀態 |

正式部署對外經 nginx 的 `8088` 埠，路徑 `/api/` 反向代理至 BFF。

---

## 2. 存取控制

**server-to-server 呼叫不需任何標頭，直接放行。** 規則與 ASR 端點完全相同，見 `asr.md` §2。

摘要：BFF 的 Origin 防護採「來源存在且不在白名單才拒」。不帶 `Origin`／`Referer` 的請求視為無來源而放行，故一般 HTTP client 不受影響。`GET` 不受此限。

---

## 3. `GET /api/tts/voices`

列出目前可用的音色。**新部署此清單為空**——系統不附任何音色，全部由操作者在管理平面建立。

### 回應

```json
{
  "voices": [
    {
      "id": "0b7f2c3e-6a41-4c9d-9f52-2b8e1d7a4c60",
      "name": "客戶-中年男性-謹慎",
      "type": "clone",
      "language": "zh-TW"
    },
    {
      "id": "9d3a1f88-5c72-4b1e-8a06-7e4c2f9b1d33",
      "name": "客戶-年輕女性-急躁",
      "type": "design",
      "language": "zh-TW"
    }
  ]
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | 字串（UUID） | 合成時傳入 `voice` 的值 |
| `name` | 字串 | 顯示名稱，全域唯一。**會被操作者改名，不可當識別鍵** |
| `type` | `"clone"` \| `"design"` | 建立方式。合成的呼叫方式兩型相同，但**情感表現力可能有差**，見下 |
| `language` | 字串 | BCP 47，例如 `zh-TW`。音色建立時指定，用於挑選適配的音色 |

**以 `id` 綁定音色，不要以 `name` 綁定。** `name` 是給人看的，操作者隨時會改。

`type` 反映音色怎麼建立的：上傳參考音為 `clone`、文字描述經 zero-shot 生成後定版為 `design`。兩型都支援 `instruct`，呼叫方式完全相同。

**但情感表現力可能不同。** design 音色的參考音必然是合成的，而情感在 voice cloning 中從參考音繼承表現力，故平板的合成參考音可能限制其情緒範圍；clone 音色若以有表情的真人錄音建立，範圍可能高得多。**此項尚未以真人參考音對照驗證**，故不寫入契約保證——挑音色時若情緒表現不足，優先換成真人錄音建的 clone 音色再試。依據見 `../superpowers/specs/2026-08-05-voxcpm2-style-control-measured.md` §5。

### 音色會消失

操作者可在管理平面刪除音色。已刪除的 `id` 再拿去合成會回 404 `VOICE_NOT_FOUND`。provider 應處理這個情況——建議在啟動時與定期重新拉取清單，並在收到 404 時重拉一次再決定是否回退到其他音色。

---

## 4. `GET /api/tts/models`

```json
{ "models": ["voxcpm2"] }
```

`POST /api/tts/speech` 的 `model` 欄位須為此清單中的值。目前只有一個。

---

## 5. `POST /api/tts/speech`

```
POST /api/tts/speech
Content-Type: application/json
```

OpenAI `/v1/audio/speech` 相容形狀，加上本專案的擴充欄位。

### 5.1 請求欄位

| 欄位 | 型別 | 必填 | 預設 | 說明 |
|---|---|---|---|---|
| `model` | 字串 | 否 | 清單中唯一值 | 取自 `GET /api/tts/models` |
| `input` | 字串 | 是 | — | 要合成的文字 |
| `voice` | 字串 | 是 | — | 音色 `id`，取自 `GET /api/tts/voices` |
| `response_format` | `"wav"` \| `"mp3"` \| `"pcm"` | 否 | `"wav"` | 輸出容器 |
| `stream` | 布林 | 否 | `false` | 分塊串流回應 |
| `instruct` | 字串 | 否 | — | Instruction：控制發聲方式的自然語言指示，寫聲學特徵而非情緒名稱，見 §5.2 |

**`input` 是純文字，控制標記由 BFF 全權處理。** BFF 會對它做文字正規化與讀音處理（數字、單位、金額展開，台灣破音字鎖定）。

送進來的文字中，**大括號 `{...}` 與半形括號 `(...)` 都會被中性化**——前者是讀音標記的保留語法，後者是風格指令的保留語法。這是安全邊界不是潔癖：`instruct` 由 BFF 組成 `(...)` 前綴併入文字送給模型，若使用者文字裡的半形括號原樣通過，一句 `(笑)` 就會變成語氣指令；而 `instruct` 裡的 `)` 也能跳出前綴注入任意內容。

需要在音訊裡唸出括號內容時，改用全形括號 `（）`。

### 5.2 `instruct` 的行為

`instruct` 由 BFF 併入送給模型的文字，**兩型音色皆生效**。

一次請求只承載一種語氣。若一段話中間要換語氣，就切成多次請求，各自帶不同的 `instruct`。

**寫聲學實現，不要只寫情緒名稱。** 這是實測結論：只給情緒標籤（`生氣`、`不耐煩`）在長度與音量上都量不到變化，聽感也無差異；給聲學描述（`語速很快、大聲吼`）則長度差 88%、音量差 55%，聽感明顯。**中文的效果強於英文**，兩個指標一致。完整數據見 `../superpowers/specs/2026-08-05-voxcpm2-style-control-measured.md`。

| 不要這樣寫 | 改成這樣 |
|---|---|
| `不耐煩` | `語速偏快、音量略大、句尾上揚` |
| `溫柔` | `輕聲、語速偏慢、句尾下沉` |
| `生氣` | `大聲、語速快、字句短促` |

`instruct` 為空或未給時，語氣由音色本身決定。

### 5.3 成功回應（`stream: false`）

HTTP 200，body 為二進位音訊，`Content-Type` 依 `response_format`：

| `response_format` | `Content-Type` | 格式 |
|---|---|---|
| `wav` | `audio/wav` | 24 kHz、單聲道、16-bit PCM，帶 44 bytes 標準 RIFF 標頭 |
| `mp3` | `audio/mpeg` | 24 kHz、單聲道 |
| `pcm` | `audio/L16` | 24 kHz、單聲道、16-bit little-endian，**無標頭** |

**wav 的取樣率、聲道數與位元深度為契約的一部分**（ADR-0003），不會因引擎更換而改變。要直接拿 PCM 的話用 `response_format: "pcm"`，比自己剝 wav 標頭可靠。

### 5.4 成功回應（`stream: true`）

HTTP 200，`Transfer-Encoding: chunked`。音訊以多個 chunk 送出，可邊收邊播。

- `response_format: "pcm"`：每個 chunk 都是裸 PCM 資料，直接接上即可。**串流建議用這個。**
- `response_format: "wav"`：第一個 chunk 含 44 bytes RIFF 標頭，其後為 PCM。標頭中的長度欄位在串流開始時未知，會填 `0xFFFFFFFF`；多數播放器可容忍，但若你們的解碼器嚴格檢查長度，改用 `pcm`。
- `response_format: "mp3"`：不支援串流，帶 `stream: true` 會回 400 `STREAM_FORMAT_UNSUPPORTED`。

**串流的結束以連線正常關閉表示**，沒有哨兵值或終止幀。

串流模式下錯誤有兩種時機：

- **送出第一個 chunk 之前**：回正常的 JSON 錯誤信封與對應狀態碼。
- **送出第一個 chunk 之後**：HTTP 狀態碼已經是 200，無法更改。BFF 直接中斷連線。

**這表示「正常結束」與「中途失敗」在協定層無法區分。** 兩者都是連線關閉。可行的偵測方式只有一種：**chunk 間的閒置逾時**——設一個門檻（建議 10 秒），超過未收到新資料即判為失敗。不要用「已收位元組數對比文字長度」來判斷，本契約不提供文字長度到音訊長度的換算，那個比例沒有量測過。

未收到任何 chunk 就中斷，一律視為失敗。

### 5.5 併發

BFF 對重量級請求有全域併發上限，預設 8。達上限時**不排隊，直接 load-shed** 回 503 `TOO_MANY_REQUESTS`。

**這個額度由 ASR 與 TTS 共用。** 陪練是辨識與合成交替進行，同一批學員的 ASR 請求會佔用 TTS 的額度，反之亦然。單一學員一次一句正常不會撞到；多人同時練習時，provider 需要退避重試。

### 5.6 延遲

**尚未量測，本契約不給承諾值。** 首音延遲與整段合成時間取決於文字長度、GPU 當下負載與是否首次載入該音色。

已知的行為特性：

- 同一個 `voice` 連續使用會命中伺服器端的音色特徵快取，第二次之後較快。頻繁在多個音色間切換會失去這個好處。
- 服務冷啟後的第一個請求明顯較慢（模型編譯與 graph capture）。
- `stream: true` 縮短的是**首音**延遲，不縮短整段完成時間。

要低延遲就用 `stream: true` + `response_format: "pcm"`，並固定使用少數幾個音色。

---

## 6. 錯誤

錯誤形狀與 ASR 端點一致：

```json
{ "error": { "code": "VOICE_NOT_FOUND", "message": "音色不存在或已被刪除。" } }
```

**成功回應是二進位音訊、錯誤回應是 JSON。** 以 `Content-Type` 判斷再決定是否解析，不要無條件當 JSON 解。

| HTTP | `code` | 觸發條件 |
|---|---|---|
| 400 | `VALIDATION_ERROR` | 缺必填欄位、型別不符 |
| 400 | `UNSUPPORTED_MODEL` | `model` 不在 `GET /api/tts/models` 清單中 |
| 400 | `UNSUPPORTED_RESPONSE_FORMAT` | `response_format` 非三個允許值 |
| 400 | `STREAM_FORMAT_UNSUPPORTED` | `stream: true` 搭配 `response_format: "mp3"` |
| 400 | `EMPTY_INPUT` | `input` 為空，或經正規化後為空 |
| 403 | `ORIGIN_FORBIDDEN` | 帶 `Origin`／`Referer` 且非白名單、非同源。server-to-server 不會遇到 |
| 404 | `VOICE_NOT_FOUND` | `voice` 對應的音色不存在或已被刪除 |
| 413 | `INPUT_TOO_LONG` | `input` 超過長度上限，`message` 含實際值與上限 |
| 502 | `TTS_UNAVAILABLE` | 模型服務連不上、回非 2xx、或回應無法解析 |
| 503 | `TOO_MANY_REQUESTS` | 重量級請求達併發上限。**不排隊，直接 load-shed** |
| 504 | `TTS_TIMEOUT` | 模型服務回應逾時 |
| 504 | `REQUEST_TIMEOUT` | 總體護欄逾時 |

### 重試建議

| `code` | 可重試 |
|---|---|
| `TOO_MANY_REQUESTS` | 是，退避後重試 |
| `TTS_TIMEOUT`／`REQUEST_TIMEOUT` | 是，但先確認文字長度是否過長 |
| `TTS_UNAVAILABLE` | 是，模型服務可能重啟中 |
| `VOICE_NOT_FOUND` | 否，但應重拉音色清單——多半是音色被刪了 |
| 其餘 400／403／413 | 否，請求本身有問題 |

**串流中途失敗不要盲目重試整段**，那會讓學員聽到重複的開頭。建議直接判定該回合合成失敗。

---

## 7. 邊界情況

| 情況 | 行為 |
|---|---|
| `input` 只有標點或空白 | 400 `EMPTY_INPUT` |
| `input` 含數字、金額、單位、英文 | 正常合成。BFF 會展開為口語形式（`3kg` → 三公斤、`NT$1,250` → 新臺幣一千二百五十元） |
| `input` 含半形括號 `(...)` 或大括號 `{...}` | 括號被中性化後才合成，不會成為語氣指令。要唸出括號請用全形 `（）` |
| `input` 含 emoji 或罕見符號 | 不發音，靜默略過 |
| `instruct` 給了但音色不支援 | 不會發生，兩型音色都支援 |
| 合成期間音色被刪除 | 該次請求仍完成（檔案在請求起始就已解析），下次請求才回 404 |
| 用非該音色 `language` 的文字合成 | 不擋。輸出品質未經驗證，挑音色時應對齊語言 |
| TTS 服務未就緒 | 502 `TTS_UNAVAILABLE`。可先查 `GET /api/health` 避免無謂請求 |

---

## 8. 範例

### 非串流

```bash
curl -X POST http://10.2.66.102:8088/api/tts/speech \
  -H "Content-Type: application/json" \
  -o out.wav \
  -d '{
    "model": "voxcpm2",
    "input": "您好，我想了解一下這張保單的內容。",
    "voice": "0b7f2c3e-6a41-4c9d-9f52-2b8e1d7a4c60",
    "response_format": "wav",
    "instruct": "音量偏小、語速平穩、句尾略上揚"
  }'
```

### 串流取 PCM

```bash
curl -N -X POST http://10.2.66.102:8088/api/tts/speech \
  -H "Content-Type: application/json" \
  -d '{
    "model": "voxcpm2",
    "input": "這個我要再考慮一下。",
    "voice": "0b7f2c3e-6a41-4c9d-9f52-2b8e1d7a4c60",
    "response_format": "pcm",
    "stream": true,
    "instruct": "語速偏慢、字間停頓略長、音量偏小"
  }' | aplay -f S16_LE -r 24000 -c 1
```

### 逐句換語氣

一次請求一種語氣，要換就分多次：

```json
[
  { "input": "喔，這樣啊。",           "instruct": "語速慢、音量小、句尾下沉" },
  { "input": "那費用大概是多少？",     "instruct": "語速偏快、音量略大、句尾上揚" }
]
```

兩次請求共用同一個 `voice`，音色一致、語氣不同。第二次會命中音色特徵快取。

### 錯誤

```json
{ "error": { "code": "TOO_MANY_REQUESTS", "message": "同時處理的請求已達上限，請稍後重試。" } }
```

---

## 9. provider 實作檢查清單

| 項目 | 要求 |
|---|---|
| 音色綁定 | 以 `id` 綁定，不以 `name` |
| 音色清單 | 啟動時拉取，收到 404 `VOICE_NOT_FOUND` 時重拉 |
| 回應解析 | 先看 `Content-Type` 再決定是否解析 JSON |
| 串流結束判定 | 連線關閉即結束，無終止幀。正常結束與中途失敗在協定層不可區分，用 chunk 間閒置逾時（建議 10 秒）偵測失敗 |
| 串流格式 | 用 `pcm`；用 `wav` 時須容忍長度欄位為 `0xFFFFFFFF` |
| 串流失敗 | 不重試整段 |
| 併發 | 處理 503，退避後重試。額度與 ASR 共用 |
| 逐句語氣 | 切句後逐句請求，共用同一 `voice` |
| 取樣率 | 固定 24 kHz、單聲道、16-bit，不需自行重取樣 |

---

## 10. 相關文件

- `asr.md` — ASR 端點規格（已實作）
- `../spec.md` — 系統規格，含 Voice 資料模型與合成行為
- `../adr/0002-design-voice-pinned-on-create.md` — Voice design 建立時定版
- `../adr/0003-rest-consumer-contract.md` — 消費端契約的形狀約束
- `../../CONTEXT.md` — 領域詞彙（Voice、Voice clone、Voice design、Instruction）
