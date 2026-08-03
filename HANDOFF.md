# Vibe-Vox 交接文件

**日期**：2026-08-04
**分支**：main，與 `origin/main` 同步於 `20b9a80`
**範圍**：專案改名、ASR 品質收尾、字級強制對齊的決策與規格

---

## 0. 一句話現況

**ASR 辨識品質已達可用水準，這條線可以結案。** 剩下的唯一缺口是時間戳——切分點連續、沒有停頓資訊，需要字級強制對齊（#25–#29，決策與契約已備妥，可直接開工）。

---

## 1. 最重要：不要再調 ASR 參數

### 品質基線（經遠端 GPU 實測）

| 項目 | 狀態 |
|---|---|
| 他語亂碼 token（`ами`／`немного`／`본`／`_UNSIGNED`） | **早在 `cc222fa` 就解決**，不再出現 |
| 輸出繁體 | 正常 |
| `max_tokens` 截斷 | 未發生 |
| 同音字錯誤 | 仍有，**熱詞可解且使用者已實測有效** |

### 別再動 prompt 參數

`918f82e`（prompt 對齊訓練格式）前後的辨識結果做過**逐字元 diff**，20 處差異全落在兩類：

- **標點 12 處**：「請問：要保人、王安蓮」→「請問，要保人王安蓮」。屬改善，原本那個頓號錯誤地把稱謂與姓名斷開
- **時間戳 8 處**：飄移 0.01–0.02 秒

**文字內容零變化。** 三處同音字錯誤（「需求未保障」應為「為保障」、「先已完成」應為「現已完成」、「系繳足為止」應為「至繳足為止」）一字不差。

結論：這類錯誤不受 prompt 影響，走熱詞。若又出現亂碼，那是**退步**，先查是否有人改動了已驗證的配置，不要重新調參。

### 已知但刻意不處理

OpenCC 用 `s2tw`（純字形），故「保單**賬**戶」不會轉成台灣慣用的「帳戶」。使用者明確要求保留辨識的實際詞彙才選 s2tw，**不要擅自改成 `s2twp`**；要修就走熱詞。

---

## 2. 本 session 完成

新→舊，全部已 push：

| commit | 內容 | 驗證 |
|---|---|---|
| `afe6f8b` | ASR 取樣率 16k→24k，對齊 plugin 目標值（#30） | ✅ GPU 實測無退步 |
| `1cf6808` | ASR API 規格、ADR-0004、CONTEXT 詞彙修正（#24） | 文件 |
| `918f82e` | ASR prompt 對齊訓練格式 + key 變體 fallback（#23） | ✅ GPU 實測無退步 |
| `da08334` | 專案改名 Vibe-Qwen → Vibe-Vox（#22） | ✅ 78 passed、docker build、容器 healthcheck |

**測試**：`cd bff && uv run pytest` → 78 passed, 4 skipped。

### 改名的影響（部署要注意）

環境變數前綴全面改為 `VIBE_VOX_`，**不保留舊前綴的相容 alias**。遠端若有自建 `.env` 檔，裡面的 `VIBE_QWEN_*` 需改名，否則設定失效退回預設值。compose 內部宣告的變數已隨改名更新。

Python package 亦由 `vibe_qwen` 改為 `vibe_vox`，git remote 已指向新 URL。

---

## 3. 下一步：字級強制對齊

### 為什麼要做

消費端 AI_practise 需要字級時間戳，用途是以自訂閾值判定停頓、再由**停頓佔全文的百分比**評判學員話術流暢度。

VibeVoice-ASR 的時間戳無法支撐：其分段是**窮盡連續切分**，段界是模型自選切點，段間間隙恆為 0。**停頓資訊在這個表示法裡不存在**——不是精度不足，是資訊本身不存在。這已由官方訓練標註（`finetuning-asr/toy_dataset/`）證實，不是缺陷。

### 票組

| 票 | 內容 |
|---|---|
| #25 | Wayfinder map（含完整選型理由與六項已確認決策） |
| #26 | T1 aligner 服務容器 |
| #27 | T2 adapter：逐段切片、batch、offset 拼接 |
| #28 | T3 合理性檢查、兩層降級、契約擴充 |
| #29 | T4 管理平面最小顯示 |

blocking edges 已用 GitHub 原生 dependencies 串好（#26 → #27 → #28 → #29）。

**從 #26 開始。** 決策見 ADR-0004，契約見 `docs/api/asr.md` 的〔規劃〕段落，詞彙見 `CONTEXT.md`。

### 選型摘要

Qwen3-ForcedAligner-0.6B（實際 0.9B 參數，Apache-2.0）。中文 AAS 33.1ms，對比 WhisperX 92.1ms、NeMo 107.5ms。時間解析度 80ms，足以分辨話術評分關心的 300ms 級停頓。以 **transformers backend 獨立部署，不動現有 vllm 容器**——官方 benchmark 本身即以 transformers 執行。

VRAM：vLLM 26–29 GB + VoxCPM2 約 8 GB + aligner 3–4 GB = 37–41 GB，RTX 6000 Ada 48GB 餘裕 7–11 GB，三模型常駐可行。aligner 的 3–4 GB 為估算值，T1 需實測取代。

---

## 4. 給接手者的警告：本 session 犯的兩個判讀錯誤

### 錯誤一：拿舊文件當現況

前一版 HANDOFF.md 寫「亂碼待驗證、辨識品質尚未達標」。我沿用了這個描述，卻沒去看使用者當下提供的實測逐字稿——那份逐字稿從頭到尾通順繁體、無任何亂碼。**亂碼早就解決了**，我卻花了一輪去修一個不存在的問題（#23）。

使用者當次回報的問題**只有時間戳切分點重複**一項。

**教訓**：使用者給的實測資料優先於任何文件描述。先看證據，再查文件。

### 錯誤二：推論到一半就當結論

發現官方 plugin 一律 resample 到 24000 後，我推論「我們送 16k 導致上採樣、產生插值假影、誘發亂碼」，寫好程式、跑完測試，差一步就讓使用者帶著錯誤的根因去 rebuild。

實際查證 `AI_practise/web/src/features/practice/audio/recorder.ts` 後推翻：其 `TARGET_RATE` 就是 16000，**參考實作那條路徑送的也是 16 kHz**，兩邊音訊相同，取樣率根本不是差異點。

**教訓**：任何「A 和 B 的差異造成 X」的推論，必須查證 A 和 B 兩邊的實際值，不能只查一邊然後假設另一邊。

---

## 5. 資源位置

`D:\pro\VibeVoice-ASR`（使用者另一專案，gemini 做的、同一台遠端 GPU、辨識率良好）：

- `server/app.py` — 實證有效的 vLLM 呼叫配置
- `VibeVoice-main/` — 完整官方 microsoft/VibeVoice repo：
  - `vibevoice/processor/vibevoice_asr_processor.py:360` — **訓練時實際使用的 prompt 組裝**（`# Build token sequence following training format`），權威度高於 test 與 demo
  - `finetuning-asr/lora_finetune.py:250-257` — `_format_transcription`，模型輸出 JSON key 的訓練目標格式
  - `finetuning-asr/toy_dataset/` — 訓練標註樣本，時間戳語義的權威來源
  - `vllm_plugin/inputs.py` — 音訊前處理，寫死 resample 至 24000、上限 61 分鐘
  - `vllm_plugin/tests/test_api.py`、`scripts/gradio_asr_demo_api_video.py` — client 契約（**兩者的 prompt keys 不同**，見下）

`D:\pro\AI_practise` — 消費端專案。錄音實作在 `web/src/features/practice/audio/recorder.ts`（16 kHz）。

### prompt 的兩套 keys 不是矛盾

- **prompt 裡的欄位描述**：`Start time, End time, Speaker ID, Content`（processor、test_api.py、gemini 實作）
- **模型輸出的 JSON key**：`Start, End, Speaker, Content`（`_format_transcription` 的訓練目標）

官方刻意如此。gradio demo 的 prompt 用了輸出 key 名，是唯一偏離訓練格式者，但它的解析端做了三重 fallback 自行吸收。本 repo 現已對齊 processor，且解析端也加了 fallback。

---

## 6. 環境與部署

- **架構**：docker compose 四單元 — bff(FastAPI) + frontend(nginx, 對外 **8088**) + vllm(VibeVoice-ASR, GPU) + tts(profile，未啟用)。字級對齊落地後會增為五單元。
- **遠端 GPU 機**：`http://10.2.66.102:8088`，RTX 6000 Ada 48GB。
- **測試環境定位**：**不必考慮多併發**，但 **prod 確定會是多併發架構**——服務須無狀態、不用全域可變狀態、不假設獨佔 GPU，屆時加 replica 即可，不得需要重寫。
- **rebuild**：`git pull && docker compose up -d --build`（模型已 bake 進 image，不會重下）。
- **本機（Windows）**：可跑 stub e2e（Origin、nginx、上傳上限），無 GPU 不能真辨識。
- **模型權重**：`microsoft/VibeVoice-ASR`（非 -HF），bake 在 `docker/vllm.Dockerfile`，served-name `vibevoice`。

---

## 7. 其他待辦

- **TTS 引擎變更**（#13 map + #14–#20）：已定案 VoxCPM2 取代 Qwen3-TTS，但 `CONTEXT.md`／`docs/spec.md`／ADR-0001 仍寫 Qwen3-TTS。七張子票未動。這是目前最大的未動工項目。
- **工作區 untracked**：`.claude/`、`.playwright-mcp/`、`spikes/`（是否 gitignore 未決）。
- **payload 放大**：轉碼後 wav 以整檔 base64 進 JSON 送 vLLM，60 分鐘音檔請求體約 230 MB。既有架構問題，短音檔無虞；接近 spec 上限時需改檔案路徑或分塊傳輸，**不要退回低取樣率**。
