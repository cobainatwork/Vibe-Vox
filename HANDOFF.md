# Vibe-Vox 交接文件

**日期**：2026-08-04
**分支**：main，與 `origin/main` 同步於 `2aa7065`
**範圍**：字級強制對齊 T1（aligner 服務容器）落地、GPU 實測、以及由此發現的既有問題

---

## 0. 一句話現況

**aligner 服務已可用並經真實 GPU 驗證，#26 已關。** 下一步是 #27（BFF 端的 adapter），開工需要的數字全部齊備。#26 期間另外發現一項既有問題，開了 #31：vLLM 的記憶體參數從未設定過，佔用可壓縮。

---

## 1. 三條不要再動的線

### 1.1 ASR 參數：不要再調

品質基線（經遠端 GPU 實測）：

| 項目 | 狀態 |
|---|---|
| 他語亂碼 token（`ами`／`немного`／`본`／`_UNSIGNED`） | **早在 `cc222fa` 就解決**，不再出現 |
| 輸出繁體 | 正常 |
| `max_tokens` 截斷 | 未發生 |
| 同音字錯誤 | 仍有，**熱詞可解且使用者已實測有效** |

`918f82e`（prompt 對齊訓練格式）前後的辨識結果做過逐字元 diff，20 處差異全落在標點（12 處，屬改善）與時間戳飄移（8 處，0.01–0.02 秒）。**文字內容零變化**，三處同音字錯誤一字不差。結論：這類錯誤不受 prompt 影響，走熱詞。若又出現亂碼，那是退步，先查是否有人改動了已驗證的配置，不要重新調參。

OpenCC 用 `s2tw`（純字形），故「保單**賬**戶」不會轉成台灣慣用的「帳戶」。使用者明確要求保留辨識的實際詞彙才選 s2tw，**不要擅自改成 `s2twp`**；要修就走熱詞。

### 1.2 aligner 的 VRAM：用實測值，不要退回估算

ADR-0004 原本寫 3–4 GB，那是估算。實測已取代它，且比估算省：

| 項目 | 實測值 |
|---|---|
| 權重載入後 idle | 2348 MiB |
| 單段對齊（4.2 秒） | +162 MiB |
| 32 段（34 秒段長）累積 | 5750 MiB |
| 日常負載 4 段累積 | 2728 MiB |

**注意這是累積量測**：同一容器內由小而大依序執行，而 PyTorch 的 caching allocator 不釋放已配置的記憶體，故各級數字是該級的**上界**而非獨立峰值。判斷安全上限時方向保守，但不可拿來做精確容量規劃。要獨立峰值需每級之間重啟容器。

量測腳本在 `aligner/scripts/bench_vram.sh`，**在 GPU 宿主上執行**（不是容器內，理由見腳本註解）。**vLLM 的配置一改就要重測。**

重測有一個會咬人的副作用：跑完 32 段後 aligner 的佔用漲到 5750 MiB 且**不會自己降回**，GPU 0 的餘裕從 5450 變成 2428。此時若試著啟動 tts 會撞到看似毫無道理的 OOM。要把記憶體還給同卡的其他服務，需 `docker compose restart aligner`。

### 1.3 `VIBE_VOX_ALIGNER_MAX_BATCH_ITEMS = 32`：別急著調

它的角色是異常防護，不是日常限制。日常負載 2 至 4 段，觸不到它。降到 8 在實際負載下沒有任何收益，因為**日常負載的峰值**本來就只有 2728 MiB（32 段那個 5750 只在異常輸入時才會發生）。

---

## 2. 本 session 完成（#26，T1 aligner 服務容器）

新→舊，全部已 push：

| commit | 內容 | 驗證 |
|---|---|---|
| `2aa7065` | 以實際使用情境修正 VRAM 與 batch 的推論 | 文件；20 passed |
| `44832e7` | batch 上限校準與量測腳本入庫 | ✅ GPU 實測 1／2／4／8／16／32 段 |
| `8b51594` | GPU 實測回填、ADR-0004 三項前提修正 | ✅ GPU 實測 VRAM 峰值與真實對齊 |
| `aa33e6d` | 修正 build 失敗（PEP 668、ensurepip） | ✅ 本機 docker build + CPU 端到端 |
| `c9739b2` | aligner 服務容器 | 20 passed（無 GPU）。**當時未做 build 驗證，見第 4 節** |

**測試**：`cd aligner && uv run pytest` → 20 passed。`cd bff && uv run pytest` → 78 passed, 4 skipped。

### 2.1 交付內容

- `aligner/`：FastAPI 薄層。`GET /health` 就緒探測（未載入回 503）、`POST /align` 多段 batch 對齊。容器內埠 **9100**
- `docker/aligner.Dockerfile`：`pytorch/pytorch:2.11.0-cuda12.8-cudnn9-runtime` + `qwen-asr==0.0.6`，權重 1.8 GB bake 進 image
- `docker-compose.yml`：第五個部署單元，掛 `device_ids: ["0"]`。**刻意不列入 bff 的 `depends_on`**，因為 aligner 不可用時 ASR 逐字稿仍須照常回傳
- `aligner/scripts/`：VRAM 量測腳本
- 根層 `.dockerignore`（aligner 的 build context 是 repo 根）與 `.gitattributes`（shell 腳本強制 LF）

契約與已查證事實記於 `aligner/README.md`，那是 #27 對接時該先讀的文件。

### 2.2 發布形式定案

採 `Qwen/Qwen3-ForcedAligner-0.6B` 走 `qwen-asr` 套件，非 `-hf` 變體。`-hf` 走 transformers 的 `AutoModelForTokenClassification`，但在官方 Transformers release 納入前需自 git 安裝 transformers，版本釘不住、build 不可重現。`qwen-asr` 0.0.6 自身釘死 `transformers==4.57.6`，是官方 model card 的首選範例，且支援 batch 與 `(np.ndarray, sr)` 輸入。兩者同屬 transformers backend，此選擇不改變 ADR-0004 的決策。理由已寫進 Dockerfile。

### 2.3 比對上游原始碼後修正的四項記載

這些都是讀 `QwenLM/Qwen3-ASR` 原始碼查證出來的，與 model card 的宣稱有出入之處以原始碼為準：

**對齊輸入上限是 180 秒**（`qwen_asr/inference/utils.py` 的 `MAX_FORCE_ALIGN_INPUT_SECONDS`），非 ADR-0004 原記的 300 秒。model card 宣稱的 5 分鐘是宣傳值。套件本身**不強制檢查**，逾限會靜默對歪，故由 aligner 服務擋下。

**標點與符號不產生 Word**。`clean_token` 只保留 Unicode 字母、數字與 `'`，其餘在送入模型前即被剝除。故 `words` 的數量**不等於** `Content` 的字元數。

**對齊單位天然是單一漢字**。`split_segment_with_chinese` 把 CJK 字元逐字切出，連續的拉丁字母與數字成一個單位。這是 ADR-0004「對齊單位為單一漢字」的實作依據，不是我們額外處理的結果。

**記憶體線性於總音訊長度，不是平方**。音訊編碼器按 `n_window * 2 = 100` frames 分塊處理、卷積亦分塊，官方 `modeling_qwen3_asr.py` 的註解即為 `Split to chunk to avoid OOM during convolution`。

另有一項對我方有利：`fix_timestamp` 已用最長遞增子序列修正逆轉的時間戳。但它修的是**單調性，不是對齊正確性**（見 3.2）。

---

## 3. 下一步：#27 aligner adapter

blocking edges 已串好（#26 → #27 → #28 → #29），#26 已關，#27 解除封鎖。

**開工前先讀這四份**：`gh issue view 27 --comments`（票本身）、`aligner/README.md`（aligner 的契約與已查證的上游行為）、`docs/adr/0004-word-level-forced-alignment.md`（決策與實測數據）、`docs/api/asr.md` 的〔規劃〕段落（消費端契約）。詞彙定義在 `CONTEXT.md`。

### 3.1 給 #27 的輸入

**不需分批。** 資料平面是回合制對話：AI_practise 錄一段話送來、ASR 轉文字、LLM 生成回應、TTS 合成後回放。單輪語音 1 至 2 分鐘，經 VibeVoice 的 30 至 40 秒切分後約 2 至 4 段，一次送完即可。

**逐段切片仍是必要的，但理由不是長度限制。** 1 至 2 分鐘遠低於 180 秒上限。真正的理由是故障隔離（單段對歪不污染其他段），且 `words` 天然對應 `segment`。整段一次對齊反而要把 `words` 依字數分配回各 segment，更複雜。

**時間基準是該段音訊自身的 0**，不是原始音檔。offset 拼接由 BFF 負責。

**語言不開放指定**，服務恆以 `Chinese` 呼叫模型。

**吞吐不是問題**：32 段（總音訊 1075 秒）耗時 1.4 秒，RTF 約 0.0013，與 ADR-0004 記載的 0.001 相符。batch 相對逐段送只快約 2.3 倍，是常數級而非數量級改善。

### 3.2 給 #28 的輸入

實測到零時長 Word 與虛假間隙的**真實案例**：官方測試音訊 13 字中，「幾」的 `Start` 與 `End` 相同（零時長），且與下一字「乎」之間有 0.16 秒間隙。「幾乎」是連讀詞，該處不應有停頓。此異常出現在 `fix_timestamp` **之後**。

對 T3 的兩項直接約束：

1. 合理性檢查**不可以「`words` 數等於 `Content` 字元數」為判準**（標點不產生 Word），否則每段都會被誤判為對齊失敗
2. 零時長與虛假間隙是必須攔下的兩種型態

整體對齊品質可用（其餘字時長 0.16 至 0.40 秒，符合中文語速）。

---

## 4. 給接手者的警告：本 session 犯的兩個判讀錯誤

### 錯誤一：能在本機驗證卻沒驗證就 push

第一版 Dockerfile 未經實際 build 就 push，讓使用者在遠端連撞兩層失敗（PEP 668、Debian 把 `ensurepip` 拆成獨立套件）。表面根因是我對 base image 的 Python 環境判斷錯誤，以為 `pytorch/pytorch` 像舊版一樣走 conda，實際是 Debian 系統 Python。

但真正的問題是**當時就能在本機驗證卻沒做**：`docker build` 不需要 GPU，只有 `docker run` 需要。我把「需要 GPU」錯誤地擴大成「整個容器都無法在本機驗證」，結果讓使用者當了 CI。

後續更發現連真實對齊都能驗：**設 `VIBE_VOX_ALIGNER_DEVICE=cpu` 即可在無 GPU 的機器上跑完整推論**，且輸出與 GPU 逐字相同（含上述零時長異常）。這條路徑寫在 `aligner/README.md`，之後驗證對齊不必佔用 GPU。

### 錯誤二：把技術上限當成設計情境

一度以「61 分鐘音檔約 100 段」推導出「必須分批」、「VRAM 一定不夠」等結論，並寫進文件與 issue。但那個 61 分鐘是 `docs/api/asr.md` 記載的**技術上限**，實際的資料平面是回合制對話、單輪 1 至 2 分鐘。

修正後峰值從外推的 13094 MiB 降為實測 2728 MiB，結論完全相反。

**教訓**：技術上限不等於設計目標。看到規格裡的極限值時，先確認實際負載長什麼樣，再決定要不要為它優化。

---

## 5. 資源位置

`D:\pro\VibeVoice-ASR`（使用者另一專案，同一台遠端 GPU，辨識率良好）：

- `server/app.py` — 實證有效的 vLLM 呼叫配置
- `VibeVoice-main/vibevoice/processor/vibevoice_asr_processor.py:360` — **訓練時實際使用的 prompt 組裝**，權威度高於 test 與 demo
- `VibeVoice-main/finetuning-asr/lora_finetune.py:250-257` — `_format_transcription`，模型輸出 JSON key 的訓練目標格式
- `VibeVoice-main/finetuning-asr/toy_dataset/` — 訓練標註樣本，時間戳語義的權威來源
- `VibeVoice-main/vllm_plugin/inputs.py` — 音訊前處理，寫死 resample 至 24000、上限 61 分鐘
- `VibeVoice-main/vllm_plugin/scripts/start_server.py` — **vLLM 的預設參數**：`gpu_memory_utilization=0.8`、`max_model_len=65536`、`max_num_seqs=64`，即 #31 的根據。行號 90-92 是依 GitHub 上的 `microsoft/VibeVoice`；本機 clone 若版本不同，搜尋 `build_vllm_command`

`D:\pro\AI_practise` — 消費端專案。錄音實作在 `web/src/features/practice/audio/recorder.ts`（16 kHz）。

**aligner 的上游**：`QwenLM/Qwen3-ASR`（GitHub）。關鍵檔案 `qwen_asr/inference/qwen3_forced_aligner.py`（`align()` 的完整實作與 `fix_timestamp`）、`qwen_asr/inference/utils.py`（`MAX_FORCE_ALIGN_INPUT_SECONDS`、音訊正規化）、`qwen_asr/core/transformers_backend/modeling_qwen3_asr.py`（音訊編碼器的分塊處理）。用 `gh api repos/QwenLM/Qwen3-ASR/contents/<path>` 讀。

### prompt 的兩套 keys 不是矛盾

- **prompt 裡的欄位描述**：`Start time, End time, Speaker ID, Content`（processor、test_api.py、gemini 實作）
- **模型輸出的 JSON key**：`Start, End, Speaker, Content`（`_format_transcription` 的訓練目標）

官方刻意如此。本 repo 現已對齊 processor，且解析端也加了 fallback。

---

## 6. 環境與部署

- **架構**：docker compose 五部署單元 — bff(FastAPI) + frontend(nginx，對外 **8088**) + vllm(VibeVoice-ASR，GPU) + **aligner**(Qwen3-ForcedAligner，GPU，埠 9100) + tts(profile，未啟用)
- **遠端 GPU 機**：`http://10.2.66.102:8088`
- **rebuild**：`git pull && docker compose build aligner && docker compose up -d aligner`。只 build 需要的服務，vllm image 裡 bake 了 7B 權重，沒必要跟著重建
- **資料庫在 volume `bff_data`（掛到 bff 的 `/data`）**，故 rebuild 不再清空 Hotword（#33 修正前會，且實際發生過數次）
- **本機（Windows）**：可跑 stub e2e，也可 `docker build`（不需 GPU）與 `VIBE_VOX_ALIGNER_DEVICE=cpu` 的完整對齊驗證
- **模型權重**：`microsoft/VibeVoice-ASR`（非 -HF）bake 在 `docker/vllm.Dockerfile`，served-name `vibevoice`；`Qwen/Qwen3-ForcedAligner-0.6B` bake 在 `docker/aligner.Dockerfile`

### 6.1 首次部署 #33 的修正：先匯出，否則現有 Hotword 會消失

**這一步只在從 #33 之前的版本升級時需要，但漏掉就沒有第二次機會。** 舊版的 DB 在容器可寫層的 `/app/var/vibe_vox.db`，新版讀的是 volume 裡的 `/data/vibe_vox.db`。套用修正時容器必然重建，舊路徑的資料不會自動搬過去，而是隨舊容器層消失。

```bash
# 1. 在「還沒 pull」的舊容器上匯出
curl -o hotwords-backup.json http://10.2.66.102:8088/api/admin/hotwords/export?format=json
# 2. 部署
git pull && docker compose build bff && docker compose up -d bff
# 3. 匯回
curl -X POST -F "file=@hotwords-backup.json" http://10.2.66.102:8088/api/admin/hotwords/import
```

若遠端目前的清單已經是空的（先前的 rebuild 已清掉），第 1 步與第 3 步可略過。

**若 bff 起不來且 log 出現 `Permission denied`**：多半是 volume 早於本次 image 就存在（曾手動 `docker volume create`，或曾以無 `/data` 的舊 image 掛過同名 volume）。此時該 volume 的 owner 已定為 root，Dockerfile 的 `chown` 不會再被繼承——需先 `docker compose down && docker volume rm <project>_bff_data` 再起（該 volume 若已有資料，先按上面第 1 步匯出）。

### 6.2 修正後仍會刪掉資料的操作

- **`docker compose down -v`**——`-v` 就是刪 volume 的意思。日常停服務用 `down` 或 `stop`，不要加 `-v`
- **`docker volume prune`／`docker system prune --volumes`**——容器已停或已移除時，這兩個會把 volume 當成無主的一併清掉。這台機器的 image bake 了 7B 權重、磁碟壓力大，清理是高機率動作，執行前先確認 bff 容器還在跑
- **搬動 repo 目錄或設 `COMPOSE_PROJECT_NAME`**——volume 實際名稱是 `<project>_bff_data`，project 名預設取自目錄名，換名等於指向另一個空 volume

備份一律走 `GET /api/admin/hotwords/export`，不要試圖直接複製 volume 裡的 SQLite 檔（WAL 模式下有 `-wal`／`-shm` 側檔，單獨複製主檔可能拿到不完整的狀態）。

### 6.3 改名的殘留風險

環境變數前綴在 `da08334` 全面改為 `VIBE_VOX_`，**不保留舊前綴的相容 alias**。遠端若有自建 `.env`，裡面的 `VIBE_QWEN_*` 需改名，否則設定失效並退回預設值。Python package 亦由 `vibe_qwen` 改為 `vibe_vox`。日後若從舊備份還原 `.env`，這條會再咬一次。

### 6.4 GPU 拓撲：兩張卡，不是一張

**這推翻了 ADR-0001 與 ADR-0004 共用的前提**，兩份都寫「單張 RTX 6000 Ada 48GB」。實測：

| 卡 | 容量 | 佔用 | 佔用者 |
|---|---|---|---|
| GPU 0 | 46068 MiB | 40618 MiB | Vibe-Vox 的 vllm（37890）+ aligner（2728，日常負載） |
| GPU 1 | 46068 MiB | 33118 MiB | gpustack 管理的 `qwen3.6-35b-a3b`、`gemma-4-12b-it-qat` |

三點要注意：容量是 46068 MiB 而非 48 GB 的標稱值；GPU 1 被**非 Vibe-Vox 的工作負載**佔著且由 gpustack 動態調度，其餘裕不可假設穩定；`docker-compose.yml` 目前把三個 GPU 服務全釘在 `device_ids: ["0"]`。

**測試環境定位**：不必考慮多併發，但 prod 確定會是多併發架構。服務須無狀態、不用全域可變狀態、不假設獨佔 GPU，屆時加 replica 即可，不得需要重寫。

---

## 7. 其他待辦

### 7.1 #31：vLLM 的記憶體參數從未設定

`docker/vllm.Dockerfile` 直接跑官方 `start_server.py`，未覆寫任何記憶體參數，故跑在上游預設 `--gpu-memory-utilization 0.8`、`--max-model-len 65536`、`--max-num-seqs 64`。而 ADR-0001／ADR-0004 一直假設 utilization 是 0.55 至 0.6。

GPU 0 日常負載下餘 5450 MiB。ADR 記 VoxCPM2 約需 8 GiB，但**那是估算值而非實測**，與餘裕同一量級，所以「放不下」目前是推測而非結論。**第一步應是實測 VoxCPM2 的實際佔用。**

四個方向記在票裡，前三者機制不同可疊加：降 utilization 減的是總量上限，降 `max-model-len` 與 `kv-cache-dtype fp8` 減的是每個 sequence 的佔用（同樣的池能容納更多併發）。`max-model-len` 對 1 至 2 分鐘的對話大幅過剩，該從 vLLM log 取實際 prompt token 數後決定。fp8 動的是數值精度，必須做逐字稿的逐字元 diff，不能只確認服務起得來。

### 7.2 TTS 引擎變更（#13 map + #14–#20）

已定案 VoxCPM2 取代 Qwen3-TTS，但 `CONTEXT.md`／`docs/spec.md`／ADR-0001 仍寫 Qwen3-TTS。七張子票未動。**這是目前最大的未動工項目。** spike harness 在分支 `spike/voxcpm-tts`。

### 7.3 ADR-0001 需重寫

它的整套 VRAM 協調論述建立在「單張卡」之上（見 6.4）。#19 是「新 ADR 取代 ADR-0001」的票，該票須納入兩張卡與第二張被別的專案動態佔用這項事實。本 session 只在 ADR-0004 記了實測，沒動 ADR-0001。

### 7.4 payload 放大

轉碼後 wav 以整檔 base64 進 JSON 送 vLLM，60 分鐘音檔請求體約 230 MB。既有架構問題，而依 3.1 的實際負載（1 至 2 分鐘）無虞。接近 spec 上限時需改檔案路徑或分塊傳輸，**不要退回低取樣率**。

### 7.5 aligner 容器以 root 執行

與 vllm 容器一致，且 `HF_HOME` 下的權重層若 chown 會整份複製一次。代價寫在 Dockerfile 註解裡：本服務以 libsndfile 解碼外部來源的音訊，而 libsndfile 有緩衝區溢位的 CVE 史。目前的緩解是該容器不對外且輸入已先經 ffmpeg 正規化。要收斂就建非 root 使用者並以其身分執行 `snapshot_download`，需重新 build 驗證。

**注意這個緩解的前提**：它建立在「aligner 不對外」之上。若日後為了 debug 在 compose 裡把 9100 映射出去，整段緩解即失效——屆時應先處理非 root，或至少限制映射的來源位址。
