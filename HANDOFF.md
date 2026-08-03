# Vibe-Qwen ASR 交接文件

**日期**：2026-08-03
**分支**：main，本地與 `origin/main` 同步於 `cc222fa`
**範圍**：#5 ASR 消費端 + 可測試環境（#21）

---

## 0. 一句話現況

ASR 端到端管線**已跑通**（上傳 → vLLM 辨識 → 回傳 → 前端三視圖顯示），但**辨識品質尚未達標**：出現他語亂碼 token 與截斷。針對品質的最後一輪修正（`cc222fa`，對齊官方 client 契約）**尚未經 GPU 實測**。

---

## 1. 最重要：先讀這個，不要重蹈覆轍

### 有一份已驗證能用的參考實作

`D:\pro\VibeVoice-ASR`（使用者另一專案，**gemini 做的、辨識率好、同一台遠端 GPU 環境**）：

- **`server/app.py`** — gemini 的 FastAPI server，呼叫 vLLM 辨識的**實證有效配置**。這是「能用的答案」。
- **`VibeVoice-main/`** — 完整官方 microsoft/VibeVoice repo：
  - `vllm_plugin/tests/test_api.py` — 官方測試，最權威的 client 契約
  - `vllm_plugin/scripts/gradio_asr_demo_api_video.py` — 官方 demo（`VibeVoiceAPIClient.transcribe_streaming`，payload 約 line 814）
  - `vllm_plugin/tests/test_api_auto_recover.py` — 官方處理 repetition loop 的機制
  - `docs/vibevoice-vllm-asr.md` — 部署文件

### 給接手者的直接建議

**優先考慮直接對接 `server/app.py` 的辨識邏輯，而不是沿用本 repo 目前的 `bff/src/vibe_qwen/adapters/vllm_asr.py`。** 理由：`server/app.py` 是使用者驗證過能用的；本 repo 的 `vllm_asr.py` 是「對照官方契約推導」的結果、**尚未經 GPU 實測**。用已驗證的東西，不要再從頭重寫後靠遠端 rebuild 慢慢試。

### 本 session 最大教訓（別再犯）

出問題時**第一步就回權威來源完整核對**（官方 test/demo/docs 三來源交叉 + 上述 gemini 實作），不要片段取用、不要先猜症狀再打補丁。本 session 因片段取用、拿未查證的解釋（「低信心片段」）當結論，導致辨識亂碼問題來回多次、耗費大量使用者時間與 token。詳見記憶 `feedback-verify-official-contract`、`reference-vibevoice-asr-impl`、`feedback-local-e2e-before-remote`。

---

## 2. ASR client 正確契約（本 session 最後對齊的狀態）

檔案：`bff/src/vibe_qwen/adapters/vllm_asr.py`

官方 payload（交叉自 `test_api.py` + gradio demo，`cc222fa` 已實作）：

| 參數 | 值 | 來源 |
|------|----|----|
| `messages[0]` system | `"You are a helpful assistant that transcribes audio input into text output in JSON format."` | 官方 |
| `temperature` | `0` | 官方（greedy）|
| `top_p` | `1.0` | 官方 |
| `max_tokens` | `int(duration*10)+100` | 動態（gemini 用同公式；官方 test 用固定 32768）|
| `repetition_penalty` | `1.1` | gemini 加值（抑制官方已知 repetition loop）|
| prompt keys | `Start, End, Speaker, Content` | 官方 demo |
| prompt 繁體指示 | `"strictly in Traditional Chinese (繁體中文)"` | gemini 加值（台灣需求）|
| duration | `ffprobe`（`_audio_duration`）| 官方 |
| 音檔 | `audio_url` data URL（轉碼後 16k wav）| 官方 |

繁體另有後處理：`bff/src/vibe_qwen/adapters/zh.py` 用 OpenCC **`s2tw`**（純字形、**不含**慣用詞轉換；使用者明確要求保留辨識的實際詞彙，故非 `s2twp`）。只轉 `segments`/`transcription_only`，`raw_text` 保留模型原始輸出。

---

## 3. 本 session commit 清單與驗證狀態

新→舊，全部已 push 至 `origin/main`：

| commit | 內容 | 驗證狀態 |
|--------|------|---------|
| `cc222fa` | ASR 完整對齊官方 client 契約（system/top_p/max_tokens/ffprobe + repetition_penalty + 繁體指示）| ⚠️ **未 GPU 實測** |
| `c3048d4` | greedy 解碼 temperature=0 | ⚠️ 未實測（已被 cc222fa 涵蓋）|
| `77e9356` | 上傳上限對齊（nginx 1MB→210MB、bff 25MB→200MB）| ✅ 本機 nginx e2e（5MB/15MB 過）|
| `bba47fe` | 簡體→繁體 OpenCC s2tw | ✅ 單元測試；⚠️ GPU 未實測 |
| `c97a818` | nginx `$host`→`$http_host`（同源比對需完整 host:port）| ✅ 本機 e2e + 使用者遠端過關 |
| `25f2f27` | OriginGuard 同源放行（動態 IP 零設定）| ✅ 本機 e2e + 使用者遠端過關 |
| `ded8863` | vllm image 用官方預設 model `microsoft/VibeVoice-ASR`（非 -HF）| ✅ 使用者遠端 build/serve 成功 |
| `6e15220` | vLLM image pin `v0.14.1`（相容 transformers v4）+ ipc host + 外部 port 8088 | ✅ 遠端驗證 |
| `9614e71` | #5 ASR client 對齊官方 vLLM 契約 + vllm image 自帶模型（開箱即用）| ✅ 基礎 |

**測試**：`cd bff && uv run pytest` → 74 passed, 4 skipped。

---

## 4. 未解問題與下一步

### 待驗證（使用者 rebuild 後）

亂碼／繁體／截斷是否已由 `cc222fa` 解決——**尚未確認**。使用者重測時要看三點：
1. 他語亂碼 token（`ами`/`немного`/`본`/`_UNSIGNED`/`LR`）是否消失
2. 輸出是否為繁體
3. 內容是否被 `max_tokens` 截斷

### 若仍有問題

- **首選**：直接對接 `D:\pro\VibeVoice-ASR\server\app.py` 的辨識邏輯（已驗證能用），取代目前的推導版。
- 若 `max_tokens` 截斷長音檔：`int(duration*10)+100` 對高密度中文可能偏緊，放寬係數或改官方 test 的固定 32768。
- 若 repetition loop：對照官方 `test_api_auto_recover.py` 的 auto-recover 機制。

---

## 5. 環境與部署

- **架構**：docker compose 四單元 — bff(FastAPI) + frontend(nginx, 對外 **8088**) + vllm(VibeVoice-ASR, GPU) + tts(profile，未啟用)。
- **遠端 GPU 機**：測試位址 `http://10.2.66.102:8088`（同 #10/#11 那台）。
- **rebuild**：`git pull && docker compose up -d --build`（模型已 bake 進 vllm image，不會重下）。
- **本機（Windows）**：有 docker，可跑 **stub e2e**（非 GPU 部分：Origin、nginx、上傳上限）——起 frontend+bff(`VIBE_QWEN_USE_STUB_MODELS=true`)、`--no-deps` 跳過需 GPU 的 vllm。但**無 GPU，不能真辨識**（辨識品質只能遠端測）。
- **模型權重**：`microsoft/VibeVoice-ASR`（非 -HF），bake 在 `docker/vllm.Dockerfile`，served-name `vibevoice`。

---

## 6. 其他待辦（本 session 未動）

- **專案改名** Vibe-Qwen → Vibe-Vox（使用者處理 github repo + 本地目錄；repo 內部引用：Python package `vibe_qwen`、docker/compose、`docs/agents/issue-tracker.md` 的 repo ref 待改）。
- **TTS**：引擎已定案 VoxCPM2（取代 spec 的 Qwen3-TTS），但 `CONTEXT.md`/`docs/spec.md` 尚未更新；wayfinder map #13（frontier tickets）未動。
- **工作區 untracked**：`.claude/`、`.playwright-mcp/`、`spikes/`（未納管，接手者自行判斷是否 gitignore）。

---

## 7. 誠實總結

本 session 花的時間遠超預期、且產出的 ASR 辨識品質仍是殘缺（最後修正未實測）。根本問題是我反覆片段取用資源、先猜後查、拿未驗證的解釋當結論，把驗證成本轉嫁到使用者的遠端 rebuild。接手者請以 `D:\pro\VibeVoice-ASR` 的能用實作為基準，不要信任本 repo 中未經 GPU 實測的辨識參數推導。
