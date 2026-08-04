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

## 驗證狀態

開發機無 GPU 仍可做端到端驗證：設 `VIBE_VOX_ALIGNER_DEVICE=cpu`。本模型只有 0.6B 且解碼為單次 NAR forward，CPU 上跑得動，故不需要卡就能驗整條推論路徑。

| 項目 | 結果 |
|---|---|
| image build | 通過。torch 2.11.0+cu128、權重 1.8 GB 已 bake 進 `/models` |
| 無 GPU 時的降級 | 載入拋 `Found no NVIDIA driver` 被捕捉，服務照常起（`restarts=0`），`/health` 回 503 `{"ready": false}` |
| CPU 模式就緒 | `/health` 回 200 `{"ready": true}` |
| 真實對齊 | 官方測試音訊（4.204 秒 @ 16 kHz）配繁體文字「甚至出現交易幾乎停滯的情況。」→ 13 個 Word，字序相符、`start` 單調遞增、末字 `end` 3.68 未超出音訊長度 |
| batch 路徑 | 兩段一次送（文字分別為全句與前半句），順序對應且 Word 數各自獨立（13 與 6），padding 不互相污染 |

送繁體文字對簡體發音的音訊仍正確對齊：模型的 CJK 逐字判定不受字形影響，故 BFF 送繁體逐字稿沒問題。

重現方式：

```bash
docker build -f docker/aligner.Dockerfile -t vibe-vox-aligner:local .
docker run -d --name aligner-cpu -p 9100:9100 \
  -e VIBE_VOX_ALIGNER_DEVICE=cpu vibe-vox-aligner:local
# 等 /health 回 200（CPU 載入約需一分鐘）
curl -X POST http://localhost:9100/align \
  -F 'items=[{"text":"甚至出現交易幾乎停滯的情況。"}]' \
  -F "audio=@asr_zh.wav"
```

測試音訊取自官方 model card：`https://qianwen-res.oss-cn-beijing.aliyuncs.com/Qwen3-ASR-Repo/asr_zh.wav`

## GPU 實測結果（2026-08-04，遠端機）

| 項目 | 結果 |
|---|---|
| image build、容器就緒 | `vibe-vox-aligner-1  Up (healthy)` |
| 真實對齊 | 輸出與本機 CPU 模式**逐字相同**，含「幾」的零時長。故 CPU 驗證是有效替代，不必為驗對齊佔用 GPU |
| **單段 VRAM 峰值** | **2348 MiB**（權重載入後 idle 2186，單段對齊 +162）。低於 ADR-0004 原估的 3–4 GB |
| 與 vllm、tts 並存 | **當前配置下不可行**，見下 |

VRAM 量測方式：在宿主上於對齊前後各讀一次。**不要**在容器內另開 process 讀 `torch.cuda.max_memory_allocated()`——那是 per-process 的，讀不到 uvicorn 那個行程的值。

```bash
nvidia-smi --query-compute-apps=pid,gpu_bus_id,used_memory --format=csv
docker ps --format "{{.ID}} {{.Names}}"   # 把 PID 對應到容器
```

### tts 無法並存，但瓶頸不是 aligner

實測的機器有**兩張** RTX 6000 Ada（各 46068 MiB），非 ADR 原記的單張 48 GB：

```
GPU 0  vllm 37890 + aligner 2348 = 40238 / 46068   餘 5830 MiB
GPU 1  gpustack 的 qwen3.6-35b 與 gemma-4-12b       餘 12934 MiB（動態調度）
```

VoxCPM2 約需 8 GiB，GPU 0 的 5830 MiB 不夠。根因是 **ADR-0004 假設 vLLM 用 `gpu_memory_utilization` 0.55–0.6，但該參數從未被實作**——`docker/vllm.Dockerfile` 直接跑官方 `start_server.py`，其預設為 `0.8`，故多吃約 10 GB。aligner 反而比估算少用 1–2 GB。

完整的實測對照與後續選項記於 ADR-0004 的 Consequences。這是 TTS 上線的前置阻礙，不屬 #26 的交付範圍。

### batch 上限仍未校準

`VIBE_VOX_ALIGNER_MAX_BATCH_ITEMS` 預設 32 仍無實測依據——上述量測只做了單段。要校準需分別量 8／16／32 段的峰值。單段推論只增 162 MiB，但 batch 的 activation 隨 padding 後的最長序列與批次大小成長，不可線性外推。
