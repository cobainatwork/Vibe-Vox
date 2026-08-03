# Aligner 服務

Qwen3-ForcedAligner-0.6B 的薄 HTTP 包裝，供 BFF 取得字級時間戳。決策與取捨見 `docs/adr/0004-word-level-forced-alignment.md`，詞彙見根層 `CONTEXT.md`。

內部服務，不對外暴露：只在 compose 的 `vibe` 網路上供 BFF 呼叫，容器內埠 `9100`。

## 端點

### `GET /health`

| 狀態 | 回應 |
|---|---|
| 權重已載入 | 200 `{"ready": true}` |
| 未載入 | 503 `{"ready": false}` |

未就緒回非 2xx 是刻意的：BFF 的探測與 docker HEALTHCHECK 都只看狀態碼。權重載入失敗（例如機器無 GPU）不會使容器 crash-loop，服務照常起、由此端點回報。

### `POST /align`

`multipart/form-data`：

| 欄位 | 型別 | 說明 |
|---|---|---|
| `items` | 字串 | JSON 陣列，每筆 `{"text": "..."}` |
| `audio` | 檔案，可重複 | wav。**順序與 `items` 一一對應**，數量須相同 |

語言不開放指定：送進來的一律是 ASR 的中文逐字稿，服務恆以 `Chinese` 呼叫模型。多一個沒人會用的參數就多兩條測試路徑，理由同 ADR-0004 否決「`words` 設為可選開關」。

一次請求即一個 batch：模型原生支援批次，逐段送反而多付固定成本。段數上限見下方設定，**預設 32 是保守起點而非實測值**——單段有秒數上限但聚合量沒有，61 分鐘音檔約 100 段一次送就撞 VRAM，而該卡由 vllm 與 tts 共用，CUDA OOM 會波及它們。傳輸層不是限制因素：已實測 multipart 可承受單段 180 秒（8.24 MiB）與 10 段共 16 MiB。

成功 200：

```json
{
  "items": [
    { "words": [ { "text": "王", "start": 0.42, "end": 0.58 } ] }
  ]
}
```

`start`／`end` 單位為**秒**（模型輸出毫秒，套件已換算並取三位小數）。時間基準是**該段音訊自身的 0**，不是原始音檔——offset 拼接由 BFF 負責。

錯誤一律 `{"error": {"code": "...", "message": "..."}}`：

| HTTP | `code` | 條件 |
|---|---|---|
| 400 | `VALIDATION_ERROR` | 缺 `items` 或 `audio` |
| 400 | `INVALID_ITEMS` | `items` 非合法 JSON、非陣列、元素非物件、或 `text` 為空 |
| 400 | `BATCH_SIZE_MISMATCH` | `items` 與 `audio` 數量不同 |
| 400 | `BATCH_TOO_LARGE` | 段數超過 `VIBE_VOX_ALIGNER_MAX_BATCH_ITEMS`（預設 32），請分批送 |
| 400 | `AUDIO_DECODE_ERROR` | libsndfile 無法解碼 |
| 400 | `AUDIO_TOO_LONG` | 單筆超過 `VIBE_VOX_ALIGNER_MAX_AUDIO_SECONDS`（預設 180） |
| 500 | `ALIGN_FAILED` | 推論本身失敗（如 CUDA OOM） |
| 503 | `ALIGNER_NOT_READY` | 權重尚未載入 |
| 503 | `TOO_MANY_REQUESTS` | 達併發上限。**不排隊，直接 load-shed**，可退避重試 |

## 對上游 `qwen-asr` 的三項已查證事實

實作前逐一比對過套件原始碼（`QwenLM/Qwen3-ASR`），與 model card 的宣稱有出入之處以原始碼為準。

**對齊單位**：`Qwen3ForceAlignProcessor.split_segment_with_chinese` 把 CJK 字元逐字切出，連續的拉丁字母與數字成一個單位。中文因此天然是字級，不需引入斷詞器——這是 ADR-0004「對齊單位為單一漢字」的實作依據，不是我們額外處理的結果。

**標點不產生時間戳**：`clean_token` 只保留 Unicode 字母、數字與 `'`，其餘字元在送入模型前即被剝除。故 `words` 的數量**不等於** `text` 的字元數。合理性檢查不可以兩者相等為判準。

**音訊長度上限 180 秒**：`qwen_asr/inference/utils.py` 的 `MAX_FORCE_ALIGN_INPUT_SECONDS = 180`，而 model card 宣稱 5 分鐘。套件本身**不強制檢查**，逾限會靜默對歪，故由本服務擋下。

另有一項對我方有利的行為：`fix_timestamp` 已用最長遞增子序列修正逆轉的時間戳，異常段落以線性內插補值。這降低但不消除合理性檢查的必要——它修的是單調性，不是對齊正確性。

## 設定

| 環境變數 | 預設 | 說明 |
|---|---|---|
| `VIBE_VOX_ALIGNER_MODEL` | `Qwen/Qwen3-ForcedAligner-0.6B` | 權重已 bake 進 image |
| `VIBE_VOX_ALIGNER_DEVICE` | `cuda:0` | `device_map` 值 |
| `VIBE_VOX_ALIGNER_MAX_AUDIO_SECONDS` | `180` | 單筆音訊上限 |
| `VIBE_VOX_ALIGNER_MAX_CONCURRENT_REQUESTS` | `1` | 同時進 GPU 的請求數；達上限 load-shed |
| `VIBE_VOX_ALIGNER_MAX_BATCH_ITEMS` | `32` | 單次請求的段數上限。**待 VRAM 實測校準** |

## 開發

```bash
cd aligner
uv sync
uv run pytest
```

測試不需 GPU：`torch` 與 `qwen-asr` 只存在於 aligner image，由 `QwenAligner.load()` 延遲 import，測試以假對齊器注入 `create_app`。真實推論只能在有 GPU 的機器驗證。

## 待遠端驗證

issue #26 的四項驗收都需要 GPU，開發機（Windows）無法執行。在遠端機（RTX 6000 Ada 48GB）依序做：

**1. image build**

```bash
docker compose build aligner   # 權重約 1.2 GB，首次 build 會下載
```

**2. 容器啟動後健康檢查回報就緒**

```bash
docker compose up -d aligner
docker compose ps aligner      # 等 healthy；start-period 為 300 秒
```

**3. 送已知音訊與文字，回得出字級時間戳**

```bash
docker compose exec aligner curl -s -X POST http://127.0.0.1:9100/align \
  -F 'items=[{"text":"甚至出現交易幾乎停滯的情況"}]' \
  -F "audio=@seg0.wav"
```

逐字檢查三件事：Word 數等於文字中的漢字數（**標點不計**）、時間戳單調遞增、末字 `end` 不超過音訊長度。

**4. 實測單段 VRAM 峰值，取代 ADR-0004 的 3–4 GB 估算值**

在宿主上於對齊前後各讀一次：

```bash
nvidia-smi --query-compute-apps=pid,used_memory --format=csv
```

看的是整個 process 的 VRAM（含 CUDA context 與 allocator 快取），這正是與 vllm、tts 共用單卡時該關心的數字。分別量單段與 8／16／32 段，據此校準 `VIBE_VOX_ALIGNER_MAX_BATCH_ITEMS`——目前的 32 無實測依據。

**5. 與 vllm、tts 同時啟動不 OOM**

```bash
docker compose --profile tts up -d
nvidia-smi --query-compute-apps=pid,used_memory --format=csv
```

實測數字須回填 ADR-0004 的 Consequences 並在 issue #26 留言，之後才可關票。
