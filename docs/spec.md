# Spec：Vibe-Vox ASR/TTS 測試與管理平台

本 spec 由 grill-with-docs 對話合成，使用 [CONTEXT.md](../CONTEXT.md) 的領域詞彙，並遵守 [ADR-0002 Voice design 定版](./adr/0002-design-voice-pinned-on-create.md)。

## Problem Statement

AI_practise（智能陪練平台）需要一個自架的 ASR/TTS 後端來驅動「AI 客戶語音對練」：學員以語音應對、系統辨識後餵 LLM 與客觀指標（語速、開口時長、流暢度），再以 TTS 讓 AI 客戶開口。其現行後端為 VibeVoice-ASR + CosyVoice 的 :8088 服務；現在要把 TTS 換成 VoxCPM2，並重新檢視傳輸方式（見 ADR-0003）。

同時，操作者需要一個地方設定與測試這套 ASR/TTS：維護會影響辨識準確度的 Hotword、上傳音檔驗證辨識、以及建立與管理可重複使用的 TTS 音色，快速驗證「換了 Hotword、換了音色、換了 Instruction」對輸出的實際影響。缺少統一介面時，這些只能靠原廠 demo 與命令列腳本零散進行。

## Solution

Vibe-Vox 是一個 ASR/TTS 後端服務，同時服務兩類消費者。**消費端資料平面**以 REST 契約供 AI_practise 呼叫，取代其現行 VibeVoice-ASR + CosyVoice 的 :8088 服務，把 TTS 換為 VoxCPM2。**管理平面**是一個統一操作頁，供操作者設定與測試同一後端。管理平面讓操作者完成四件事：

1. 管理 Hotword 清單（新增、編輯、刪除、啟用切換、匯入、匯出），辨識時系統自動把啟用中的 Hotword 編譯成 context 注入 ASR。
2. 上傳常見格式音檔做 ASR 測試，取得帶語者與時間戳的 Who/When/What 結果，並可臨時覆寫本次 context。
3. 做 TTS 測試：輸入文字、選語言、選音色、下 Instruction（附範例），即時試聽與下載。
4. 管理音色：以參考音檔建立 Voice clone、以文字描述建立並定版 Voice design，並可試聽、編輯、刪除。系統不附任何音色，全部由人建立。

介面誠實反映模型能力邊界（能力感知）。

## User Stories

### Hotword 管理

1. 作為操作者，我想檢視所有 Hotword 的清單與其啟用狀態，以便掌握目前會套用哪些詞彙。
2. 作為操作者，我想新增一筆 Hotword（term 與選填 note），以便提升特定專有名詞的辨識準確度。
3. 作為操作者，我想編輯既有 Hotword 的 term 與 note，以便修正或補充說明。
4. 作為操作者，我想刪除不再需要的 Hotword，以便維持清單精簡。
5. 作為操作者，我想個別啟用或停用某筆 Hotword，以便在不刪除的情況下暫時不套用它。
6. 作為操作者，我想一次啟用或停用多筆 Hotword，以便快速切換情境。
7. 作為操作者，我想把 Hotword 清單匯出成 CSV，以便在外部編輯或備份。
8. 作為操作者，我想把 Hotword 清單匯出成 JSON，以便程式化處理。
9. 作為操作者，我想從 CSV 匯入 Hotword 並看到重複項如何被處理，以便批次建立清單。
10. 作為操作者，我想從 JSON 匯入 Hotword，以便還原或遷移清單。
11. 作為操作者，我想預覽本次辨識實際會套用的 context 字串與其 token 估算，以便確認不會吃掉過多 64K 預算。
12. 作為操作者，我想搜尋或過濾 Hotword，以便在大量詞彙中快速定位。

### ASR 語音轉文字測試

13. 作為操作者，我想上傳常見格式音檔（wav、mp3、m4a、flac、ogg 等），以便直接測試手邊的音訊。
14. 作為操作者，當我上傳不支援的格式時，我想看到清楚的錯誤與可接受格式清單，以便更換檔案。
15. 作為操作者，我想送出辨識並得到結果，以便驗證辨識品質。
16. 作為操作者，我想看到帶語者與時間戳的 Segment 分段結果（Who/When/What），以便檢視多語者對話。
17. 作為操作者，我想看到去除語者標記的純文字，以便直接複製全文。
18. 作為操作者，我想看到模型的原始輸出，以便除錯異常結果。
19. 作為操作者，我想讓本次辨識自動套用目前啟用中的 Hotword，以便驗證 Hotword 的效果。
20. 作為操作者，我想在本次辨識臨時覆寫或追加 context，以便測試特定提示而不改動清單。
21. 作為操作者，我想一鍵複製辨識結果文字，以便貼到別處使用。
22. 作為操作者，我想把辨識結果匯出（JSON 或純文字），以便留存。
23. 作為操作者，我想看到辨識耗時與音檔長度，以便評估效能。
24. 作為操作者，當音檔超過可用長度上限時，我想看到清楚提示，以便自行裁切。上限由辨識逾時決定（`VIBE_VOX_ASR_TIMEOUT_SECONDS`，預設 300 秒對應約 26 分鐘音檔），**不是**模型的 61 分鐘技術上限，見 `docs/api/asr.md` §3.3 與 #35。
25. 作為操作者，當辨識失敗時，我想看到 Status、Root Cause、Suggested Fix 的錯誤，以便自行排除。

### TTS 文字轉語音測試

26. 作為操作者，我想輸入要合成的文字，以便測試發音效果。
27. 作為操作者，我想選擇語言，以便符合文字內容。
28. 作為操作者，我想從依 Clone / Design 分組的下拉選單選擇音色，以便比較不同音色。
29. 作為操作者，我想對任一音色輸入描述發聲方式的 Instruction（音量、語速、句尾走向），以便控制表現。
30. 作為操作者，我想在音色管理頁看到「Voice clone 走可控風格模式而非最高保真模式」的說明，以便理解聲線相似度的取捨來自平台決策，而不誤判為 clone 品質不良。
31. 作為操作者，我想看到並一鍵套用 Instruction 範例，以便快速上手指令寫法。
32. 作為操作者，我想送出合成並取得音訊，以便試聽。
33. 作為操作者，我想在頁面內嵌播放合成結果，以便即時聆聽。
34. 作為操作者，我想下載合成的 wav 檔，以便留存或外部使用。
35. 作為操作者，當合成失敗時，我想看到 Status、Root Cause、Suggested Fix 的錯誤，以便排除。

### 音色管理

36. 作為操作者，我想檢視所有音色，以便掌握可用音色。
37. 作為操作者，我想上傳參考音檔與逐字稿建立 Voice clone 音色（含名稱與語言），以便重複使用自訂音色。
38. 作為操作者，我想以文字描述建立 Voice design 音色並在建立時定版，以便得到穩定可重現的音色。
39. 作為操作者，我想試聽任一音色，以便決定是否採用。
40. 作為操作者，我想重新命名音色，以便維持可辨識的命名。
41. 作為操作者，我想更換 Voice clone 的參考音檔或修改其逐字稿，以便修正 clone 品質。
42. 作為操作者，我想修改 Voice design 的描述並重新定版，以便調整音色。
43. 作為操作者，我想刪除自建音色，以便清理不需要的項目。
44. 作為操作者，當我建立的音色名稱與既有名稱重複時，我想看到清楚錯誤，以便改名。
45. 作為操作者，當參考音檔格式或長度不符需求時，我想看到清楚提示，以便更換。

### 系統與維運

46. 作為操作者，我想看到健康檢查顯示 ASR 與 TTS 兩個模型服務的就緒狀態，以便確認系統可用。
47. 作為操作者，當某個模型服務尚未就緒時，我想在相關頁面看到清楚提示與停用送出，以便避免無效操作。

## Implementation Decisions

### 架構與模型服務

- 系統由五個部署單元組成：React + Vite 前端（nginx）、FastAPI BFF、vLLM 承載 VibeVoice-ASR、獨立服務承載 Qwen3-ForcedAligner、vLLM-Omni 承載 VoxCPM2。全部以 Docker Compose 編排，透過 nvidia-container-toolkit 取用 GPU。機器有兩張卡；GPU 1 被非本專案的工作負載動態佔用，其餘裕不可假設穩定，故 GPU 服務全釘在 `device_ids: ["0"]`。
- BFF 為應用後端（不載入模型），內部以模組邊界明確拆分三塊職責：(1) API 與編排（請求驗證、路由、呼叫模型端點）、(2) 檔案與轉碼（上傳落地、FFmpeg 轉碼、參考音存取）、(3) 持久化（SQLite 存取）。稱其相對於模型服務為「薄」是指不承載模型，而非職責少；採模組化單體，不向微服務拆分。
- 模型呼叫藏在兩個 adapter 介面後面：`AsrClient` 與 `TtsClient`。BFF 只依賴這兩個介面，實作負責與 vLLM／vLLM-Omni 溝通。此介面同時是唯一的測試 stub 邊界。
- ASR 辨識走 vLLM 的 `/v1/chat/completions`（傳 audio 與 context prompt，回傳 Who/When/What 結構化 JSON）。不使用 `/v1/audio/transcriptions`，因其尚未實作且標準 schema 會弄丟 Speaker。
- TTS 合成走 vLLM-Omni 的 OpenAI 相容 `/v1/audio/speech`，服務以 `vllm serve openbmb/VoxCPM2 --omni` 啟動。
- 參考音經擴充欄位 `ref_audio` 以 `data:` base64 傳入。不用 `file://`，因其需 server 開 `--allowed-local-media-path`，會擴大容器的檔案系統暴露面。
- 參考音時長的伺服器端限制為 1.0 至 30.0 秒，超界時端點回的是 `ValueError` 文字而非本專案的錯誤碼。**這條限制在建立音色時驗，不在 adapter 送出前驗**：參考音在建立時就固定了，驗一次就夠，而只有建立時的操作者能修正它——合成時失敗只會讓消費端依契約重試一個永久失敗。可用性（容器合法、可解碼、時長合規）因此是 Voice 的不變量。既有音色不受該不變量涵蓋，故音色清單逐列回報 `unusable_reason`，合成端點以**同一組判準**做 backstop 並回 409 `VOICE_UNUSABLE`——兩處各寫一組判準會長出「清單說不可用而合成照樣送出」的組合。
- 參考音的大小上限與辨識用上傳解耦（30 秒的上界容不下 200 MiB 的檔案）。合成路徑會把整個參考音讀進記憶體並編成 base64，故該讀取不得跑在 event loop 上——否則一個大參考音會讓每一次合成都停住整個 BFF。
- **Instruction 以 `(...)` 前綴寫進 `input`，不經任何欄位。** `instructions` 與 `task_type` 對 VoxCPM2 不生效且不報錯——送了會得到 HTTP 200 加一段未套用該風格的音訊。BFF 不送這兩個欄位。
- 一次 request 只承載一種風格，逐句不同語氣即逐句一次呼叫。切句與組風格前綴在 `TtsClient` 之上完成，`synthesize()` 收已切好的句子與各自風格，中文斷句規則不進 HTTP client。
- 端點回 48 kHz mono，adapter 降為消費端契約要求的 24 kHz／mono／16-bit。
- VRAM 協調：`gpu_memory_utilization` 是 per-instance 上限，不是同卡多實例瓜分的總額。約束為「本實例啟動當下的 free memory ≥ total × 自己的 utilization」，不足時在啟動期直接拒絕而非推論期 OOM。KV 尺寸以 `kv_cache_memory_bytes` 顯式封頂，封頂後不看 utilization。ASR 側現值 0.65——2026-08-07 實測 0.70 之下 TTS 在啟動期被拒，降到 0.65 三者才共存（**閒置常駐**，同時推論未測）。各服務實佔與量測的限制見 #31。

### Hotword（單一扁平清單）

- Hotword 是本專案自有的結構化資料，型別 shape：

  ```
  Hotword = { id, term (必填), note?, enabled (預設 true), created_at, updated_at }
  ```

- 辨識時，系統把所有 `enabled` 為真的 `term` 編譯成一段 context 字串，經由 `AsrClient` 以 prompt 注入。前端 token 估算僅為提示；BFF 必須在組裝 context 後於伺服器端強制檢查 token 預算，超出安全上限直接拒絕（HTTP 413 或 400），不得送往 vLLM。此檢查不可只在 UI 層，以防繞過介面直接呼叫 API。
- 匯入採 upsert 策略：以 `term` 為自然鍵，重複者更新、不存在者新增；匯入結果需回報新增與更新筆數。匯入端點須設定可設定的檔案大小與筆數上限（超過回 413 或 400），並以串流解析、分批交易寫入，避免整檔載入記憶體，以及單一長時間獨佔寫鎖引發全系統 SQLITE_BUSY。
- Hotword 內容視為不可信輸入。建立與匯入時須過濾或跳脫可能與底層 ASR／LLM 特殊 token 衝突的保留標記（如 `<|...|>`、語者或時間戳標記）與控制字元；注入 context 時使用者文字須以字面（literal）方式 tokenize，不得被解讀為特殊 token，以免破壞 prompt template 或 Who/When/What 的 JSON 結構。BFF 亦須對模型回傳的非預期輸出做防禦性解析，不得因解析失敗直接回 500。

### Voice 音色（依 ADR-0002）

- Voice 型別 shape：

  ```
  Voice = { id, name (唯一), type ('clone'|'design'), language,
            ref_audio_path,     // 兩型皆有：clone 為上傳的參考音，design 為定版擷取的生成音
            ref_text?,          // clone 的參考音逐字稿，管理用 metadata
            instruct?,          // design 的原始描述，定版後為 metadata
            created_at, updated_at }
  ```

- 參考音檔存於檔案系統，metadata 存於資料庫，不以 DB blob 儲存音檔。
- 建立 Voice design 時執行一次生成、擷取輸出音檔存為 `ref_audio_path`（定版）；之後 clone 與 design 音色在合成時共用同一條 clone 播放路徑。定版音檔以端點原生的 48 kHz 存放，不存降採樣後的版本。
- 建立 clone 與 design 音色為跨元件操作（模型呼叫加 DB 寫入），採「先產物後落庫」順序：先成功產生並落地參考音檔（clone 為驗證後的上傳檔、design 為定版生成檔）於暫存區，確認成功後才寫入 DB 紀錄並把檔案移入正式路徑；任一步失敗則清除暫存檔且不留 DB 紀錄，避免產生缺少 `ref_audio_path` 的損壞列。
- 刪除自建音色時，實體參考音檔採軟刪除或移入待清理區，不在刪除當下立即 `rm`：先移除 DB 紀錄使新的合成無法再引用，實體檔由背景或啟動時的清理程序於寬限期後回收。合成一律在請求起始解析檔案且模型呼叫序列化，以縮小刪除與讀取的競態窗口，避免進行中的合成因檔案消失而崩潰，同時防止只刪 DB 造成的儲存空間洩漏。
- **合成一律走 Controllable 模式**：送出參考音但**不送 `ref_text`**，兩型音色都吃 Instruction。此為平台固定行為，不暴露為使用者選項，故 Instruction 欄位無停用態。代價是放棄 Hi-Fi 模式的聲紋相似度，該差異的量級未實測。
- 模式由「送不送 `ref_text`」決定，與音色型別無關：送了就落到 Hi-Fi 並忽略 Instruction，且**失效是靜默的**（回 200、有音訊、風格未套用）。`ref_text` 僅為管理用 metadata，不得進入合成路徑。

### 消費端契約（給 AI_practise，REST）

此平面的端點形狀對齊 AI_practise 既有 provider 期望，為約束性契約（見 ADR-0003）：

- ASR：`POST /api/asr/transcribe`，multipart 音訊 → `{ segments:[{Start,End,Speaker,Content}], ... }`。回合制批次辨識（VibeVoice-ASR 單次批次），非邊講邊出的即時 partial。
- Hotwords：`GET`／`POST`／`DELETE /api/hotwords`，消費端形狀為 `{id, word}`（`POST` body `{word}`、`DELETE` 回 `{success:true}`）。管理平面在同一資源額外提供 `note`／`enabled`／匯入匯出，但不改變消費端既有欄位與行為。
- TTS：`GET /api/tts/models`、`GET /api/tts/voices`（`{voices:[{id,name,type,language}]}`）、`POST /api/tts/speech`（OpenAI 相容加擴充：`model, input, voice=id, response_format: wav|mp3|pcm, stream, instruct`）→ 二進位音訊；wav 為 24kHz／mono／16-bit，`pcm` 為同規格的裸資料；`stream=true` 時分塊回應，結束以連線正常關閉表示。完整契約見 `docs/api/tts.md`。
- 對齊現行部署慣例：地端、無認證/TLS。ASR 由現行 WebSocket 改為 REST，AI_practise 需新增 REST 版 AsrProvider（provider 可插拔，成本低）。

### 管理平面 API 契約（與唯一測試 seam）

- Hotword：列出、建立、更新、刪除；匯出（`format=csv|json`）；匯入（上傳檔案，含檔案大小與筆數上限）；context 預覽（回傳編譯後字串與 token 估算）。匯入匯出欄位契約固定為 `term`、`note`、`enabled`：CSV 需具此三欄標頭，JSON 為物件陣列且欄位同名；`enabled` 缺省視為 true。
- ASR：辨識端點接收 multipart 音檔與選填的本次 context 覆寫，回傳結構：

  ```
  { segments: [ { Start, End, Speaker, Content } ], raw_text, transcription_only,
    duration, applied_context }
  ```

- TTS：列出音色；建立 clone（multipart：name、language、參考音、選填 ref_text）；建立 design（name、language、instruct，伺服器端定版）；更新音色；刪除音色；合成端點接收 text、language、voice_id、選填 instruct，回傳 wav；Instruction 範例端點回傳策展清單。`instruct` 由 BFF 組成 `(...)` 前綴併入送給模型的文字。
- 系統：健康檢查端點回報兩個模型服務的就緒狀態。
- 統一回應與錯誤格式：成功回 `data`，錯誤回帶 `code` 與 `message` 的結構，錯誤語意對齊 Status／Root Cause／Suggested Fix。
- 資源與狀態碼：所有接受上傳的端點宣告 `max_file_size` 與請求逾時；超過檔案上限回 413，context 超出 token 預算回 413 或 400，格式或標頭驗證失敗回 400，音色名稱重複回 409。

### 音檔處理

- 音檔輸入視為不可信資料。以 ffmpeg 接收常見格式（wav、mp3、m4a、flac、ogg、webm 等），統一轉為模型所需取樣率後再送入 `AsrClient`。驗證規則：以檔案標頭（magic numbers）與 MIME 判定型別，不可只信副檔名；FFmpeg 呼叫加 `-protocol_whitelist file` 並禁用 concat、http 等協定，避免 SSRF 與本機檔案讀取；使用者提供的任何字串（檔名、音色名、ref_text）一律不得拼進 FFmpeg 參數或路徑。無法解碼的檔案回明確錯誤。

### 持久化

- 採 SQLite 單檔資料庫（啟用 WAL 模式並設定連線 busy_timeout，約 5000ms，以緩解併發寫入的 database locked／SQLITE_BUSY），儲存 Hotword 與 Voice metadata。參考音檔與合成輸出存於檔案系統的固定資料夾；所有落地檔名一律由伺服器生成 UUID，儲存路徑不得由使用者輸入（含原始檔名、`Voice.name`）推導，`Voice.name` 僅為顯示名稱。
- **容器化部署須把資料庫置於掛載的 volume，不得留在容器可寫層。** 留在可寫層時任何建立新容器的操作（`docker compose build` 後 `up`、`--force-recreate`、`down` 後 `up`）都會連同資料一起丟棄，而那正是日常部署流程——此事已實際發生數次（#33）。資料目錄須與暫存目錄分開：暫存音檔用畢即刪、隨容器銷毀正是預期行為，混進持久化 volume 只會被 200 MB 級的上傳檔撐大。
- **掛載點必須在 image 裡就存在且屬執行身分。** named volume 首次掛載時會從 image 的對應路徑繼承 owner 與權限；該路徑不存在時 Docker 建出的 volume 屬 root，而 BFF 以非 root 執行，會在建檔時以 Permission denied 啟動失敗。故 Dockerfile 須先 `mkdir` 並 `chown`，compose 才掛 volume——只做後者服務會起不來。

### 安全與資源邊界

- 信任邊界：本系統部署於公司內部伺服器、供受信任的操作者使用，認證與授權刻意排除（見 Out of Scope）。此假設須明示記錄，防禦以「輸入驗證」與「資源上限」為主，而非存取控制。若日後對外網開放，須重新評估並加上認證。
- 瀏覽器邊界：系統經瀏覽器操作且無認證，雖無 cookie 型 ambient 憑證（故非典型 CSRF），但未受保護的內網 API 仍可能被操作者所開啟的第三方網頁跨來源驅動（尤其免預檢的簡單請求）。BFF 須設定嚴格 CORS（僅允許前端 Origin、不開放萬用字元），狀態變更端點要求 JSON 以觸發預檢，並檢查 Origin／Referer 標頭以擋下第三方網站發起的偽造請求。
- 上傳資源上限：API 契約明訂單檔 `max_file_size` 上限；上傳一律以串流寫入磁碟，不整檔載入記憶體，避免 BFF OOM；超過上限回 HTTP 413。
- 併發與逾時：模型呼叫在 `AsrClient`／`TtsClient` 層序列化，並限制 BFF 同時處理的重量級請求數（上傳、轉碼、辨識、合成）；每個重量級端點定義明確請求逾時與逾時後的資源清理，避免同步長連線耗盡連線與磁碟。
- **逾時必須由內而外遞增，且反向代理不得依賴其預設值。** 順序為「子系統逾時 < BFF 總體 guard < 反向代理逾時」：內層先觸發，使用者才拿到帶 `code` 的 JSON 錯誤信封；反向代理先觸發則拿到它的 HTML 錯誤頁，且無從得知真正的失敗原因。nginx 的 `proxy_read_timeout` 預設 60 秒曾因此成為整個系統音檔長度的實際上限（約 5 分鐘），而該值從未被任何人選擇過（#35）。此順序須有測試保護：`bff/tests/test_config.py` 會實際讀取 `frontend/nginx.conf` 比對，設定被移除或調小都會紅。
- **子進程呼叫一律需要逾時，且不得同步阻塞 event loop。** `subprocess.check_output` 這類同步呼叫會凍住 event loop，使 `asyncio.timeout` 無法觸發：屆時整個請求只剩反向代理能收尾，上一條的順序保證即失效。一律用 `asyncio.create_subprocess_exec` 並包 `asyncio.timeout`，逾時後強制 kill（見 `audio/transcode.py` 與 `adapters/vllm_asr.py`）。
- 子進程管理：FFmpeg 以子進程呼叫，須設定子進程等級逾時，並在逾時或 HTTP 連線中斷時強制終止（SIGKILL）底層 FFmpeg 程序。HTTP 逾時只斷前端連線、不會自動殺子進程；掛起的解碼器若殘留在背景將持續佔用 CPU，累積後拖垮容器。
- 磁碟保護：暫存與輸出資料夾需有清理策略（辨識輸入用畢即刪、合成輸出可設保留上限），避免磁碟被逐步填滿。此外，BFF 啟動序列須清理過期暫存檔，以回收因硬崩潰或強制重啟而遺留、尚未被「先產物後落庫」清除流程處理到的孤兒檔案。

## Testing Decisions

- 好的測試只驗證對外行為，不驗證實作細節。對本專案，對外行為即 BFF HTTP API 的請求與回應。
- 唯一測試 seam 為 BFF HTTP API。所有整合測試在此層進行，涵蓋 Hotword CRUD 與 context 編譯、匯入匯出、ASR 辨識編排、TTS 合成、音色 CRUD 與 design 定版、以及「兩型音色皆接受 Instruction 且 `ref_text` 不進入合成路徑」。
- `AsrClient` 與 `TtsClient` 在測試中以 stub 取代，回傳固定的假結果（例如固定的 Segment 陣列、固定的 wav 位元組），使 HTTP API 測試離線、確定性、無需 GPU。
- 需測試的模組：BFF 的路由與編排邏輯、Hotword context 編譯器、音檔轉檔器（以小型樣本檔）、合成請求的組裝（Instruction 進入文字、`ref_text` 不進入）。
- 前端主要共用同一組 HTTP API 契約做測試。User Story 30 的模式揭露是純前端呈現、無對應 API 行為，故對它保留一個最低限度的元件測試為必要項，而非選配。
- 先驗證條件（每個 User Story 對應一條可檢查的 API 行為）：辨識回傳含 Segment 與純文字；停用的 Hotword 不進 context；clone 音色送 Instruction 被接受且該 Instruction 進入送給模型的文字；任一音色的合成請求都不含 `ref_text`；design 建立後產生 `ref_audio_path`；超過 `max_file_size` 的上傳回 413；context 超出 token 預算回 413 或 400；夾帶路徑穿越字元的檔名或音色名不影響儲存路徑（落地檔名為 UUID）；非音訊或偽造副檔名的檔案（magic number 不符）被拒；design 建立時若模型呼叫失敗，DB 不留殘缺列且暫存檔被清除；跨來源（非前端 Origin）的狀態變更請求被 CORS 或 Origin 檢查拒絕；BFF 啟動後過期暫存檔被清理；超過上限的 Hotword 匯入被拒（413 或 400）；含特殊 token 或控制字元的 Hotword 被過濾或跳脫而不破壞辨識輸出解析；刪除音色後不影響進行中的合成，且不殘留未追蹤的實體檔。
- Prior art：本 repo 為全新專案，尚無既有測試可參照；此 seam 策略即為後續測試的範本。

## Out of Scope

- 邊講邊出的即時 partial ASR（live caption／barge-in）。ASR 為回合制批次辨識（VibeVoice-ASR 單次批次），經 REST 回傳完整逐字稿；真正的增量串流需換串流 ASR 模型，屬未來。TTS 串流回應則在範圍內（見消費端契約）。
- 針對超長音檔的非同步 job 佇列與進度回報。初版採同步處理，並以「單檔大小上限、請求逾時、併發上限、串流落地」作為同步模式下的資源護欄（見 Implementation Decisions 的安全與資源邊界）。
- 多使用者帳號、認證與權限控管。初版為內部單一操作情境。
- 模型微調（fine-tune）與訓練。
- VibeVoice 的 TTS 能力（原廠已於 2025 年 9 月移除），以及 VoxCPM2 以外的 TTS 引擎。
- 行動版 App 與桌面原生封裝。
- 介面語系擴充（初版僅台灣繁體中文）。

## Further Notes

- TTS 文字前處理層：任意台灣繁中送進 VoxCPM2 前需經 `raw 繁中 → 淨化 {} → TN → OpenCC s2tw → G2P（只鎖台陸差異字，輸出 {pinyin+聲調}）→ VoxCPM2(normalize=False)`，**順序不可倒置**——TN 會把 `{ni3}` 展開成 `{ni三}`，標記全毀。TN 用 `wetext`（Apache-2.0，已在 VoxCPM2 相依樹）加台灣規則補丁，G2P 用 `g2pw` 的發行模型（台灣注音）。該層放在 `TtsClient` 之上：純函式、無 I/O，放進 adapter 會使其無法在不接模型的情況下單測。細節見 `docs/superpowers/specs/2026-08-05-tts-text-frontend-tn-g2p.md`。
- 本地無 GPU 開發模式：測試用的 `AsrClient`／`TtsClient` stub 應同時可用於本地開發。提供 `docker-compose.dev.yml`（或等效設定）在無 48GB GPU 的工作機上以 stub adapter 啟動前端與 BFF，讓日常 UI／BFF 開發脫離高階 GPU 環境，降低 onboarding 摩擦。
- 消費端遷移：Vibe-Vox 取代 AI_practise 現行 :8088（VibeVoice-ASR + CosyVoice）。`/api/tts/speech` 形狀不變，`/api/tts/voices` 的回應形狀改變，AI_practise 需一併調整；ASR 由 WebSocket 改 REST，需新增 REST 版 AsrProvider（現有 provider 可插拔設計支援）。兩 repo 皆由 Claude 協同開發，可一併處理。詳見 ADR-0003。
- Instruction 範例需策展一份清單，對應 User Story 31。**範例一律以中文撰寫且寫聲學實現，不寫情緒名稱**：實測顯示只給情緒標籤（`生氣`、`不耐煩`）量不到變化也聽不出差異，給聲學描述（`語速很快、大聲吼`）則長度差 88%、音量差 55%，且中文效果強於英文。數據見 `docs/superpowers/specs/2026-08-05-voxcpm2-style-control-measured.md`。
- Hotword context 與音訊共用 64K token 預算，UI 的 token 估算是防止準確度反被稀釋的重要防線。
- 原廠資源與連結（模型權重 HF id、vLLM 與 vLLM-Omni 服務文件、transformers ASR 文件、VoxCPM2 模型卡與 readthedocs）見 `docs/superpowers/specs/` 的兩份 2026-08-05 findings，其中逐條標明 primary 與 community。
