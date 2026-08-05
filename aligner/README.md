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

一次請求即一個 batch：模型原生支援批次，逐段送反而多付固定成本。

**實際負載很小。** 資料平面是回合制對話（AI_practise 錄一段話 → ASR → LLM → TTS → 回放），單輪語音 1–2 分鐘，經 VibeVoice 的 30–40 秒切分後約 2–4 段。段數上限（預設 8，見「上限已由 32 降為 8」）的角色是 VRAM 保護，不是日常限制。傳輸層更不是限制：已實測 multipart 可承受單段 180 秒（8.24 MiB）與 10 段共 16 MiB。

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
| 400 | `BATCH_TOO_LARGE` | 段數超過 `VIBE_VOX_ALIGNER_MAX_BATCH_ITEMS`（預設 8），請分批送 |
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

### tts 能否並存尚未確定，但瓶頸不是 aligner

實測的機器有**兩張** RTX 6000 Ada（各 46068 MiB），非 ADR 原記的單張 48 GB：

```
GPU 0  vllm 37890 + aligner 2728（日常負載 4 段） = 40618 / 46068   餘 5450 MiB
GPU 1  gpustack 的 qwen3.6-35b 與 gemma-4-12b                       餘 12934 MiB（動態調度）
```

ADR-0004 記 VoxCPM2 約需 8 GiB，但**那是估算值而非實測**。餘裕 5450 MiB 與它同一量級，所以要先實測 VoxCPM2 的實際佔用才能判斷是否放得下——不能只憑估算就斷定不行。

上表寫於 2026-08-04，當時 vLLM 的 `gpu_memory_utilization` **從未被顯式設定**、跑在上游 `start_server.py` 的預設 0.8，而 ADR-0001 一直假設 0.55–0.6，故 vLLM 多吃約 10 GB。aligner 反而比估算少用 1–2 GB。

2026-08-05 三個記憶體參數已搬到 `docker-compose.yml` 並由 `.env` 覆寫，utilization 調至 0.70。**故上表的餘裕數字已過期**，最新狀態與待確認項見 HANDOFF.md 的 2.4 與 8.2（#31）。

完整的實測對照與後續選項記於 ADR-0004 的 Consequences 與 #31。不屬 #26 的交付範圍。

### batch 上限已校準

以 34 秒段長、104 字（貼近 VibeVoice 實際切出的段落）逐級量測。用官方那個 4.2 秒的測試音訊量會嚴重低估。

| batch | VRAM | 增量 | 耗時 |
|---|---|---|---|
| idle | 2348 MiB | — | — |
| 1 | 2628 MiB | +280 | 0.1s |
| 2 | 2628 MiB | +0 | 0.1s |
| 4 | 2728 MiB | +100 | 0.2s |
| 8 | 3170 MiB | +442 | 0.3s |
| 16 | 4016 MiB | +846 | 0.6s |
| 32 | **5750 MiB** | +1734 | 1.4s |

重現：`aligner/scripts/bench_vram.sh`（在 GPU 宿主上執行）。

**上表是累積量測，各級數字為該級的上界。** 量測在同一容器內由小而大依序執行，而 PyTorch 的 caching allocator 不釋放已配置的記憶體，故「4 段 2728 MiB」實為「跑過 1、2、4 段之後」的值，單獨跑 4 段可能更低。這對判斷安全上限是保守的方向，但不可拿來做精確的容量規劃——要各級的獨立峰值，需每級之間重啟容器。

**記憶體線性於總音訊長度，不是平方。** 音訊編碼器按 `n_window * 2 = 100` frames 分塊處理、卷積也分塊（`modeling_qwen3_asr.py` 的 `chunk_num = ceil(feature_lens / (n_window * 2))`，官方註解寫 `Split to chunk to avoid OOM during convolution`），所以不存在全序列 attention 的平方成長。

**上表用的是同一段 34 秒音訊重複 N 次，這一點決定了它不能外推。** 批次張量會 pad 到該批最長的段落，均勻輸入的 padding 浪費恰好 1.00 倍，真實錄音則不然。見下方「上限已由 32 降為 8」。

**下面這組推算已於 2026-08-05 被實測推翻，保留以說明它錯在哪：**

```
aligner 可用上限 = 46068 - 37890 (vLLM) = 8178 MiB
32 段（異常防護上限）實測 5750  →  餘 2428 MiB
4 段（日常負載）實測 2728       →  餘 5450 MiB
邊際成本 1734 / 16 ≈ 108 MiB/段  →  64 段約需 9218 MiB，超出可用量
```

實跑時的實際數字：GPU 總量 45465 MiB（不是 46068）、vLLM 佔 38615 MiB（不是 37890），故 aligner 可用只有 **6850 MiB**，那 2428 MiB 的餘裕不存在。而 aligner 實際衝到 6799 MiB 才 OOM，**超過上表列的 5750**。兩個假設同時失效。

**呼叫端分批（#36 起）**：日常負載的 2–4 段一次送完即可，但超過 32 段的輸入並非假想情境。10 分鐘、五語者的會議錄音切出 63 段（平均 9.65 秒，語者切換處必然切段，且有大量 0.77 至 1.6 秒的應答段），整批送出換來 400 `BATCH_TOO_LARGE`，全 63 段拿不到時間戳。

此處原本記載「不需分批」，那是以單語者回合制對話的段長（30 至 40 秒）為前提。**該前提對多語者會議不成立**，而錯誤訊息本身就是「請分批送」，呼叫端卻沒有那個能力。`bff` 的 `HttpAlignerClient` 現依 `max_batch_items` 分批，並維持批次級的故障隔離：某批失敗不丟棄其他批的結果。

分批的代價小：32 段（總音訊 1075 秒）只花 1.4 秒，RTF ≈ 0.0013，與 ADR-0004 記載的 0.001 相符；batch 相對逐段送約快 2.3 倍，是常數級改善而非數量級。

### 上限已由 32 降為 8（2026-08-05 實測）

分批本身有效，但 **cap 32 在真機上 CUDA OOM，兩批都失敗**。63 段扣掉 6 個非語音標記段後送出 57 段，切成 32 + 25，兩批都在推論時撞牆（`ALIGN_FAILED`）。

根因是**段數對記憶體的主導變數是盲的**。批次張量 pad 到該批最長的那一段，而上表的校準用同一段 34 秒音訊重複 32 次，padding 浪費恰好 1.00 倍。真實錄音的段長是 1.77 至 41.29 秒：

| 上限 | 批數 | 最大單批 padding 後 | 對照校準 1088s |
|---|---|---|---|
| 4 | 15 | 165.2s | 0.15x |
| **8** | **8** | **330.3s** | **0.30x** |
| 16 | 4 | 660.6s | 0.61x |
| 24 | 3 | 991.0s | 0.91x |
| 32 | 2 | 1206.1s | 1.11x |

57 段的實際音訊只有 621 秒，cap 32 之下被 pad 成 1206 秒，接近翻倍。cap 8 實測通過。

**仍有約 700 MiB 無法歸因**：線性外推 cap 32 只得到約 6100 MiB，實測 6799 MiB。可能是累積量測的 5750 本身不可靠、每段的固定開銷，或第一批 OOM 後 caching allocator 保留的區塊讓第二批一開始就沒空間。故 8 取保守值而非貼著算出來的線。

**8 是這台測試機的值，不是通用的設計值。** 這台機器是單張 48 GB 且三個模型共用，日常負載 2–4 段觸不到任何上限，會撞到的只有管理平面的長音檔測試，那條路多幾次往返只差幾秒，所以在此調高沒有收益。規格不同的部署可以調高，但**必須以真實錄音的段長分布重測**，不可沿用上表（見該表上方的偏差說明）。

逐段切片仍是必要的，但理由不是長度限制（1–2 分鐘遠低於 180 秒上限），而是故障隔離：單段對歪不污染其他段，且 `words` 天然對應 `segment`。整段一次對齊反而要把 `words` 依字數分配回各 segment。

vLLM 的 `gpu_memory_utilization` 若調整，上表的餘裕與上限都要重算。
