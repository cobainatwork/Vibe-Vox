# Vibe-Vox 交接文件

**日期**：2026-08-05
**分支**：main（工作樹乾淨，本 session 全部已 merge 並 push）
**範圍**：字級強制對齊 T2–T4 落地（#27 #28 #29）、Hotword 持久化（#33）、對齊判準過嚴（#34）、逾時鏈路（#35）、會議錄音揭露的三個缺陷（#36 #37 #38）、真機驗收與兩組校準推翻（#32 #36）、vLLM 記憶體參數顯式化（#31）

---

## 0. 一句話現況

**字級強制對齊從選型到落地全部走完**（#25 map 與 #26–#29 四張子票均已關閉），消費端契約 `docs/api/asr.md` 的〔規劃〕欄位全部生效。

期間修掉四個會讓功能實質不可用的缺陷，**四個都是使用者實測回報的，測試一個都沒抓到**：Hotword 每次 rebuild 就消失（#33）、合理性檢查對長段落過嚴使 9 段錄音只有 2 段通過（#34，修正後同一份錄音全段通過）、nginx 逾時從未設定使逾 5 分鐘的音檔必然 504（#35）、會議錄音的 63 段超過 batch 上限而 adapter 未實作分批使全段無法對齊（#36）。

另兩個是診斷 #36 時從那份錄音的資料分析出來的，性質不同：#37 是可觀測性（錯誤丟棄服務端已經給出的原因，診斷只能靠反推），#38 是資料正確性但**當時碰巧未發作**（非語音標記段會產生假的字級時間戳，這次剛好被時長上界攔下）。兩者都不是使用者回報的症狀，而是查一件事時看到的第二、第三件事。

**字級對齊那條線已在真機驗收完畢。** 會議錄音 63 段中 53 段對齊、2481 個字級時間戳，`aligned_duration` 532.24／610.39 秒。過程中實測推翻了兩組校準（batch 上限、單字時長下界的浮點邊界），兩者都已修正並驗證，見 2.2、2.3 與 4.1。

**vLLM 的三個記憶體參數也在本 session 第一次被顯式設定**（原本寫在 image 內的上游腳本裡），utilization 由 0.8 調至 0.70 並實測驗證辨識文字逐字元不變，見 2.4。

**目前這台機器上「ASR 加 aligner、非併發」的功能已完整驗證。** TTS 尚未實作（compose 裡是 placeholder image、BFF 的 client 是 stub），故「三個模型共存」無法驗證，其 VRAM 帳也算不出來，見 8.2。

**接手的第一件事：#32 需要第二份真人樣本，而它最好是練習錄音。** 目前的閾值餘裕很薄：通過段落的異常佔比最高 28.6%，距上限 30% 只有 1.4 個百分點。且跨距判準對首末段的行為只有含刻意開頭沉默的練習錄音能驗（會議錄音通常已在進行中才開始錄）。理由與判別方法見 8.1.1，**不必等接上 AI_practise**。

**最大的未動工項目是 TTS 引擎變更**（#13 map + 七張子票 #14–#20，加下游的 #6–#8 共十張）：它同時解掉三份文件與已定案決策的不一致、解封 #6–#8、也讓 #31 的 VRAM 帳有辦法算。

---

## 1. 部署前必做：先匯出 Hotword，否則會消失

**這件事有時效，漏掉就沒有第二次機會。** #33 的修正改變了資料庫路徑：舊版在容器可寫層的 `/app/var/vibe_vox.db`，新版在 volume 裡的 `/data/vibe_vox.db`。套用修正時容器必然重建，舊路徑的資料不會自動搬過去，而是隨舊容器層消失。

```bash
# 1. 在「還沒 pull」的舊容器上匯出
curl -o hotwords-backup.json "http://10.2.66.102:8088/api/admin/hotwords/export?format=json"
# 2. 部署
git pull && docker compose build bff && docker compose up -d bff
# 3. 匯回
curl -X POST -F "file=@hotwords-backup.json" http://10.2.66.102:8088/api/admin/hotwords/import
```

若遠端目前的清單已經是空的（先前幾次 rebuild 已清掉），第 1 步與第 3 步可略過。這三條指令在本機完整模擬過整條升級路徑，逐條實測有效。

之後就不必再匯出了：rebuild 不會再清空。但仍有三種操作會刪掉 volume，見 7.2。

---

## 2. 四條動之前先讀完的線

### 2.1 ASR 參數：不要再調

品質基線（經遠端 GPU 實測）：

| 項目 | 狀態 |
|---|---|
| 他語亂碼 token（`ами`／`немного`／`본`／`_UNSIGNED`） | **早在 `cc222fa` 就解決**，不再出現 |
| 輸出繁體 | 正常 |
| `max_tokens` 截斷 | 未發生 |
| 同音字錯誤 | 仍有，**Hotword 可解且使用者已實測有效** |

`918f82e`（prompt 對齊訓練格式）前後的辨識結果做過逐字元 diff，20 處差異全落在標點（12 處，屬改善）與時間戳飄移（8 處，0.01 至 0.02 秒）。**文字內容零變化**，三處同音字錯誤一字不差。結論：這類錯誤不受 prompt 影響，走 Hotword。

**若有人回報辨識退步，先確認 Hotword 還在不在，再考慮任何參數。** #33 修正前每次 rebuild 都會清空 Hotword，而 Hotword 消失不報錯，只會讓準確度悄悄退回沒有 Hotword 的狀態，這正是會誘導人去調參的假象。若又出現亂碼，那是退步，先查是否有人改動了已驗證的配置。

OpenCC 用 `s2tw`（純字形），故「保單**賬**戶」不會轉成台灣慣用的「帳戶」。使用者明確要求保留辨識的實際詞彙才選 s2tw，**不要擅自改成 `s2twp`**；要修就走 Hotword。

### 2.2 aligner 的 VRAM：舊的那組數字已被實測推翻，不要拿它規劃容量

ADR-0004 原本寫 3 至 4 GB 是估算，2026-08-04 的量測取代了它：

| 項目 | 當時量到 |
|---|---|
| 權重載入後 idle | 2186 MiB |
| 單段對齊（4.2 秒）峰值 | 2348 MiB |
| 32 段（34 秒段長）累積 | 5750 MiB |
| 日常負載 4 段累積 | 2728 MiB |

**2026-08-05 的實跑推翻了由它推出的容量結論**，兩個假設同時失效：

| 項目 | 當時推算 | 實際 |
|---|---|---|
| GPU 總量 | 46068 MiB | 45465 MiB |
| vLLM 佔用 | 37890 MiB | 38615 MiB |
| aligner 可用餘裕 | 8178 MiB | **6850 MiB** |
| 32 段的佔用 | 5750 MiB | **6799 MiB 後 OOM** |

當時算出的 2428 MiB 餘裕不存在，而 aligner 的實際佔用又超過表上的 5750。**這組數字只能當歷史紀錄，不能當容量依據。**

失效的原因有兩層。表面上是 vLLM 長大、總量比記錄少。更根本的是**量測用同一段 34 秒音訊重複 N 次**，而批次張量會 pad 到該批最長的段落：均勻輸入的 padding 浪費恰好 1.00 倍，真實錄音接近 2 倍。上表因此系統性低估真實負載。完整資料見 `aligner/README.md` 的「上限已由 32 降為 8」。

量測腳本在 `aligner/scripts/bench_vram.sh`，**在 GPU 宿主上執行**。要重測就得用真實錄音而非重複的合成負載，否則會再犯同一個錯。

還有一個會咬人的副作用：跑完大批次後 aligner 的佔用**不會自己降回**。此時若試著啟動 tts 會撞到看似毫無道理的 OOM。要把記憶體還給同卡的其他服務，需 `docker compose restart aligner`。

### 2.3 `VIBE_VOX_ALIGNER_MAX_BATCH_ITEMS = 8`：在這台機器上不要調高

**8 是實測值，取代原本校準出的 32**（32 在真機上 OOM，見 2.2）。日常負載 2 至 4 段，觸不到它；會撞到的只有管理平面的長音檔測試，而那條路多幾次往返只差幾秒。

**這是這台測試機的值，不是通用的設計值。** 這台機器是單張 48 GB 且三個模型共用，所以在此調高沒有收益，只是把 aligner 往 vLLM 留下的天花板推。規格不同的部署可以調高，但必須以真實錄音的段長分布重測，不可沿用 2.2 那組數字。

**這個值有兩個讀取者**：BFF 用它決定怎麼分批，aligner 用它決定拒絕什麼。compose 以同一個表示式餵給兩個服務，**只調一邊就會回到 #36 的故障**（超出的批次換來 400，該批全段拿不到時間戳）。要調就改 `.env` 的單一項，兩邊會一起變，`bff/tests/test_config.py` 會讀兩邊的設定檔比對。

### 2.4 vLLM 的三個記憶體參數：只有 utilization 會還記憶體給 GPU

上游 `start_server.py` 的預設是 `--max-model-len 65536`、`--max-num-seqs 64`、`--gpu-memory-utilization 0.8`。**那三個值寫在 image 內的上游腳本裡，連 grep 都找不到**，而 ADR-0001 至今仍寫著 utilization 該壓到 0.55 至 0.6。這與 #35 的 nginx 預設 60 秒是同一個失效模式。

已搬到 `docker-compose.yml` 的 `command:`，值從 `.env` 進來，`bff/tests/test_config.py` 守住「三個都必須顯式出現」。

**先看 token 預算的算法，三個值都由它推出。** `max_model_len` 是**輸入加輸出的總預算**，vLLM 以 `prompt + max_tokens` 驗證，超過即回 400。一段 T 秒音檔需要：

```
音訊 token    = 7.5T + 3      （上游 vllm_plugin：ceil(samples / 3200) + 3，24 kHz）
max_tokens    = 10T + 100     （adapters/vllm_asr.py）
prompt 其餘   ≈ 100
合計          ≈ 17.5T + 203
```

| 參數 | 上游預設 | 現值 | 依據 |
|---|---|---|---|
| `--max-model-len` | 65536（約 62 分鐘） | **24576**（約 23 分鐘） | 使逾時（約 20 分鐘）仍是較先觸發的那一層，音檔長度上限的行為不變 |
| `--max-num-seqs` | 64 | **8** | 對齊 BFF 的 `max_concurrent_heavy_requests` |
| `--gpu-memory-utilization` | 0.8 | **0.70** | KV cache 由 9.92 降至 5.52 GiB，辨識文字逐字元不變 |

**65536 不是隨意的值**：61 分鐘音檔（上游註解明列的模型上限）需要 64253 tokens，它剛好包住。所以降它是**刻意放棄一個我們不用的能力**，不是修正別人的疏失。

**量 vLLM 的佔用一律帶 `gpu_uuid`，並確認 vLLM 已完全起來。** 本機有兩張卡，不帶 GPU 欄位無法分辨；而啟動途中的讀數會嚴重偏低（實測相差 8466 MiB），據它下結論會得到錯的模型。

```bash
nvidia-smi --query-compute-apps=pid,gpu_uuid,used_memory --format=csv
```

**已量到的（2026-08-05，皆為完全啟動後）**：

| | 原始（0.8、65536、64） | 現在（0.70、24576、8） |
|---|---|---|
| KV cache | 9.92 GiB／185,680 tokens | **5.52 GiB／103,264 tokens** |
| vLLM 行程佔用 | 38615 MiB | **33654 MiB** |

**釋出 4961 MiB**（38615 的來源是 0.8 時代的 CUDA OOM 訊息，非 `gpu_uuid` 查詢，故該側精度較低）。

行程佔用比 utilization 給的預算（45465 × 0.70 = 31825）多約 1.8 GB，那是 CUDA context 等 vLLM 帳外的部分。**這個關係只有兩點資料，不要拿它做容量規劃**，要數字就重新量。

**辨識行為以那份 10 分鐘會議錄音驗證過**，結論分兩層：

- **文字完全穩定**：63 段、53 段對齊、2481 字，`transcription_only`、每段 `Content`、每個 Word 的 `Text` **全部逐字元相同**。先前擔心的「批次組成改變使近乎平手的 token 翻面」沒有發生。
- **時間戳不穩定，且幅度比預期大**：332 個時間戳欄位有差異，**最大 0.47 秒**（第 61 段 `Start` 573.96 → 573.49），53 個欄位差超過 0.05 秒。ASR 自身的切點只動 ±0.01 秒，但切片邊界跟著位移後，**aligner 把它放大了約 47 倍**。

第二點對 #32 有意義：**字級時間戳的跨次重現性只到約 0.5 秒**，評分端不能假設比這更精細的可靠度。彙總值反而穩定（`aligned_duration` 532.24 → 532.56，差 0.06%）。

**同一份腳本還有另一個問題**：它每次啟動都跑 `apt-get` 與 `pip install`（實際 log 可見 `Requirement already satisfied`），使容器執行期依賴網路、且 image 不可重現。腳本有 `--skip-deps` 可跳過，但需先確認它是否也跳過 pip 那步。追蹤於 #41，未動工。

---

## 3. 本 session 完成

新→舊：

| 合併 commit | 票 | 內容 |
|---|---|---|
| `dbe3399` | #31 | vLLM 三個記憶體參數搬出 image、utilization 0.8 → 0.70（見 2.4） |
| `5bd47f0` | #32 #36 | batch 上限 32 → 8、單字時長下界改為容差比較（見 2.2、2.3） |
| `c77ae1b` | #36 #37 #38 | 分批送出、錯誤攜帶服務端原因、非語音標記段不送對齊 |
| `63c3411` | #35 | nginx 逾時未設，逾 5 分鐘的音檔必然 504 |
| `e6815ac` | #34 | 判準分兩層，單字時長異常不再使整段失敗 |
| `b8333e0` | #33 | Hotword 資料庫改存 volume |
| `84645e7` | #29 | ASR 測試頁顯示對齊狀態與字級時間戳 |
| `d54d665` | #28 | 合理性檢查、兩層降級、消費端契約擴充 |
| `04d52ac` | #27 | aligner adapter：逐段切片、batch、offset 拼接 |

全部已 merge 並 push，工作樹乾淨。

**測試**：`bff` 151 passed／4 skipped、`aligner` 21 passed、`frontend` typecheck + 36 passed + production build。nginx 配置經 `nginx -t` 驗證，`docker compose config` 通過。

**真機驗收**：那份 10 分鐘會議錄音在本 session 跑了四次（cap 32 OOM → cap 8 通過但 21 段被攔 → 容差修正後 53 段對齊 → utilization 0.70 後逐字元不變）。軌跡見 4.1。

開了十張票：**#32**（閾值校準，下界已完成、餘三項待第二份樣本）、**#33 #34 #35 #36 #37 #38**（皆已修並關閉）、**#39**（`transcription_only` 是否過濾標記段，待產品判斷，見 4.1）、**#40**（跨距判準對多語者簡短應答段誤判）、**#41**（vLLM 容器執行期跑 apt 與 pip），後三張未動工。

### 3.1 #27 aligner adapter

`bff/src/vibe_vox/adapters/aligner.py` 的 `HttpAlignerClient` 與 `bff/src/vibe_vox/audio/slice.py`。

**切片不起 ffmpeg**，改用 stdlib `wave` 做 byte 層切片：輸入恆為 `AudioIntake.transcoded()` 的 pcm_s16le wav，格式是內部不變量，切片不需重新編碼，而每段 spawn 一個子進程要付逾時處理與檔案落地的代價。副效果是「全程不落暫存檔」自動滿足。

**切片 buffer 預設 0.5 秒**，左右各留以吸收 VibeVoice 切點漂移（段界是模型自選切點，可能落在某個字的發音中間）。該值是推算而非實測，見 5.2。

**故障隔離**：服務端對整個 batch 是全有全無，任一筆不合契約即整批回錯。空 `Content`（模型缺欄位時補空字串，`docs/api/asr.md` §6）會換來 400、零長度切片使推論失敗回 500，兩者都會毀掉同批正常段落。故退化段落在送出前剔除、結果中留空位。

#36 之後這裡多了兩層：剔除條件加入非語音標記段（#38），且隔離從段落級升到批次級，因為 `align()` 現在會分批（見 4.1）。

### 3.2 #28 合理性檢查與兩層降級

`bff/src/vibe_vox/alignment.py`。**判準分兩層**（#28 原本沒有這個分層，是 #34 的實測逼出來的，見 3.6）：

| 型態 | 判準 | 層級 |
|---|---|---|
| 單字時長異常 | 落在 `[0.08, 2.0]` 秒之外 | **局部**：只累計佔比，單獨出現不使整段失敗 |
| 單字時長異常佔比過高 | 超過三成的字落在界外 | 結構：那不是雜訊而是系統性對歪 |
| 時間戳逆轉 | 後字 `Start` 早於前字 `End` | 結構 |
| 時間戳脫勾 | 落在該段切片涵蓋的範圍外 | 結構（防禦性下界，正常路徑不觸發） |
| 擠壓型對歪 | 對齊跨距不足段落切點跨距的一半 | 結構，**僅適用於既非首段也非末段的段落** |
| 零長度段落 | `End <= Start` | 結構 |
| 字級清單為空 | 上游未對齊或 adapter 剔除了退化段落 | 結構 |

**跨距判準排除首末段**的理由值得記住：頭尾沉默是本系統明文要保留的預期情境（開頭沉默本身即話術缺失、結尾沉默有兩種語義需消費端判斷）。學員按下錄音後沉默 20 秒才開口，首段跨距比約 0.4；講完忘記按停止，末段同理。若對它們套跨距判準，正常段會被標為對齊失敗並丟棄 `words`，而那正是消費端唯一能算出頭尾沉默的資料。代價是首末段的擠壓型對歪抓不到，記於 #32。

`span_applies` 的條件是 `0 < index < last`，故**單段錄音（既首亦末）完全不套跨距判準**。這在只切出一段的短錄音會發生。

**第二層降級不映射狀態碼**：對齊服務不可用、逾時或回非 JSON，都在端點層攔下並回 200 與完整逐字稿、全段 `aligned: false`。`main.py` 刻意**不**註冊 `AlignerUnavailable` → 502 的 handler，別以為那是漏的。

**一項刻意的取捨**：`speech_start`／`speech_end` 僅計 `aligned: true` 的段落。未對齊段沒有發音邊界可用，唯一替代是填切點，而第一段切點恆為 `0.0`，算進去等於宣稱「沒有開頭沉默」，那才是假資訊。代價是首末段未對齊時頭尾沉默被**高估**且無訊號，已寫入 `docs/api/asr.md` §4.4／§4.5 並指明消費端該自查 `segments[0].aligned`。

### 3.3 #29 管理平面顯示

`frontend/src/AsrPanel.tsx` 的對齊狀態欄與字級摺疊。

**摺疊不能用 `<details>`**：它的內容在未展開時仍會被 React 渲染進 DOM，只是視覺隱藏，成本已經付了，不滿足票要求的「避免一次渲染全部」。改用 state 控制的條件渲染，且逐段獨立（以 `Set<number>` 記錄展開的索引）。有一條測試專門守住「展開一段不渲染其他段」，否則這層防護等於沒有。

**不顯示 `alignment` 四個彙總數字**：那是給評分端組分母用的，操作者判斷「有沒有對齊、數字合不合理」不需要。要查的人仍可從「匯出 JSON」取得完整回應。

額外加了「全段未對齊」的整體警示：對齊服務掛掉時後端回 200，故障不會表現為錯誤訊息，而 `/api/health` 也不含 aligner，管理平面沒有別的訊號。

### 3.4 #33 Hotword 持久化

應用層的持久化本來就完整（SQLite + WAL + `busy_timeout`，`docs/spec.md` 有專章），斷的是部署層：bff 服務沒掛任何 volume，資料只存在容器可寫層。

修正需 Dockerfile 與 compose 兩邊配合，**順序不能顛倒**：named volume 首次掛載時從 image 的對應路徑繼承 owner，而 `/app/var` 在 image 裡不存在（是 runtime 建的）。只在 compose 加 volume 會得到 root 擁有的 volume，而 BFF 以 uid 1001 執行，服務會在建檔時 `Permission denied` 起不來。故 Dockerfile 先 `mkdir /data` 並 `chown`，compose 才掛上去。

DB 與暫存目錄刻意分開：暫存音檔單檔上限 200 MB、用畢即刪，隨容器銷毀正是預期行為，混進持久化 volume 只會把它撐大。故 `temp_dir` 仍為 `/app/var/tmp`。

### 3.5 #35 逾時鏈路：nginx 從未被設定過

使用者以 10 分鐘會議錄音測試辨識，回 504。**模型沒有失敗，是 nginx 不等了。**

`frontend/nginx.conf` 的 `/api/` location 沒有設任何 timeout，走 nginx 預設的 `proxy_read_timeout 60s`，而 BFF 的 guard 是 240 秒。決定性證據在 log：504 當下 `vllm-1` 仍 `Running: 1 reqs`、`50.9 tokens/s`，且錯誤是 `while reading response header from upstream`（讀回應超時，不是連線失敗）。

**60 秒的 nginx 上限使音檔的實際可用長度只有約 5 分鐘**，而前端的警示閾值寫 60 分鐘、`docs/api/asr.md` 宣稱支援 61 分鐘。三者差一到兩個數量級，且沒有任何地方記載過真正的上限。

先前的 214 秒音檔會成功是因為它剛好在線內：`max_tokens = duration × 10 + 100` 為 2246，實際輸出約 2000 tokens，在 50 tokens/s 下約 40 秒。

修正後的逾時鏈路（**內層必須先觸發**，否則使用者拿到 nginx 的 HTML 錯誤頁而非 BFF 的 JSON 錯誤信封）：

| 層 | 值 | 位置 |
|---|---|---|
| ffprobe 子進程 | 30s | `adapters/vllm_asr.py` 的 `_FFPROBE_TIMEOUT_SECONDS` |
| `asr_timeout_seconds` | 300 | `config.py` |
| BFF guard | 504（三者之和 × 1.2） | `Settings.heavy_request_budget()` |
| nginx `proxy_read_timeout` | 600s | `frontend/nginx.conf` |

**guard 含 1.2 倍餘裕不是隨意的**：三者之和只涵蓋轉碼、辨識與對齊，而 guard 還包住 200 MB 級的上傳讀取、wav 長度讀取與 base64 編碼。零餘裕時 guard 可能先於 `asr_timeout` 觸發，使用者拿到籠統的 `REQUEST_TIMEOUT` 而非精確的 `ASR_TIMEOUT`。

`test_config.py` 的 `test_reverse_proxy_timeout_exceeds_heavy_guard` **會實際去讀 `frontend/nginx.conf`**，不是斷言字面值。這點很重要：#35 的根因是那幾行設定**根本不存在**，字面值的斷言防不了那種失效。已實測驗證該測試在「設定被移除」與「值小於 guard」兩種情況都會紅。

**ffprobe 那層是審查抓出來的**：它原本用 `subprocess.check_output` 且無 `timeout=`，同步阻塞 event loop。ffprobe 掛住時 guard 的 `asyncio.timeout` 根本無法觸發，整個請求只剩 nginx 能收尾，正是 #35 要消除的結果。已改為 asyncio 子進程加逾時，與 `audio/transcode.py` 同一模式。

**61 分鐘從來不可能達成**：那需要 `asr_timeout` 約 750 秒。未如此設定的理由是長逾時會讓掛住的請求佔住 GPU 與連線，而實際負載是回合制對話的 1 至 2 分鐘。

**可用上限的推導修正過一次。** 原先寫「26 分鐘」，用的是 214 秒／40 秒的實測比例（係數 5.35）；但那與 `max_tokens` 的上限（係數 5.0）混用了兩個基準。最壞情況下 300 秒只容得下 1490 秒，而那還是打平點：生成恰好用完整個逾時，沒有餘裕給 base64、prefill 與傳輸。**前端警示與文件現在都取 20 分鐘**，20 到 25 分鐘之間是「可能成功也可能 504」的灰帶。

超限的音檔永遠拿不到成功回應，所以事後的長度警示救不到它們。504 的錯誤訊息現在會附上長度脈絡（`asr.ts` 的 `errorMessage`），操作者才知道該裁切而非重試。

### 3.6 #34 判準過嚴：實測推翻了 #28 的一項設計

**使用者的實測資料直接推翻了零容忍的單字時長判準。** 保險話術錄音、語音乾淨、辨識品質良好、語速正常，9 個 Segment 中只有 2 個通過對齊檢查：

| 段落 | 段長 | 字數 | 結果 |
|---|---|---|---|
| 139.05–149.77 | 10.72 秒 | 42 | 已對齊 |
| 166.93–175.65 | 8.72 秒 | 33 | 已對齊 |
| 其餘 7 段 | 5.94–39.59 秒 | 最長約 190 | 全部未對齊 |

**通過的兩段都是短段。** 這是累積機率問題：單字異常率若為 1%，190 字的段落至少出現一個異常的機率是 85%，40 字只有 34%。首段與末段不套跨距判準，故它們的失敗只能來自單字時長判準，而第一段約 190 字、39.59 秒，平均每字 0.208 秒，語速完全正常。

根因是把常態雜訊當成故障訊號：#26 那個 13 字樣本裡就有一個零時長（「幾」），異常率 7.7%，而它在 `fix_timestamp` 之後仍出現，屬模型的常態輸出。零容忍讓「單字異常污染整段」，與 ADR-0004「單段對歪不污染其他段」的意圖相反，也使字級對齊實質不可用（評分端拿不到任何停頓資料）。

修法（使用者選定寬鬆語義）：判準分兩層，見 3.2 的表。**單字時長異常只在佔比超過三成時才使整段失敗。**

同時把 `is_sane` 改為 `find_word_defect`，回傳 `Defect(code, detail)` 而非布林並寫進 log。`code` 是穩定的機器可讀值（供 #32 統計各判準的攔下次數），`detail` 給人看：

```
第 3 段未通過對齊檢查（implausible_duration_ratio）：單字時長異常佔比 45%（9／20 字）超過上限 30%，例如「幾」0.00 秒、「乎」0.01 秒
```

原本 `aligned: false` 不說明為什麼，無從判斷是模型對歪還是判準過嚴。這也是 #32 校準要收集的資料。

**已驗證**：部署後用同一份錄音重跑，9 段全部通過（原本只有 2 段）。修正有效。

但**診斷本身仍未取得直接證據**：全段通過等於沒有任何 defect 被記錄，log 印不出「原本是哪個判準在攔」。「失敗來自單字時長判準」是從「首末段不套跨距 + 語速正常」推論的，至今仍是推論。這不影響修正的正確性，但表示 #32 校準時不能把它當已知條件。

另一個限制：這份錄音是 TTS 合成語音，它證明了修正解除了誤判，沒有證明閾值對真人語音合適，理由見 8.1.1。

---

## 4. 下一步

**4.1 已在真機驗收完畢**，過程中實測推翻了兩組校準（見 2.2、2.3），留在這裡是因為那三個根因各自推翻了一個先前的判斷。**4.2 是最大的未動工項目。**

### 4.1 #36 #37 #38：會議錄音揭露的三個缺陷（已修並已真機驗收）

由使用者實測 10 分鐘、五語者的會議錄音發現，三張票都動 `bff/src/vibe_vox/adapters/aligner.py`，已一輪做完並 merge 進 main（`c77ae1b`）。

**已在真機驗收完畢，但過程中實測推翻了兩組校準。** 錄音在 `D:\SAP\華厚線上發票問題討論-20250521_141801-會議錄製_0~10.wav`，共三次重跑：

| 次 | 條件 | 結果 |
|---|---|---|
| 1 | cap 32（校準值） | **CUDA OOM，兩批都失敗**，全段未對齊。分析見 2.2 |
| 2 | cap 8 | 通過。57 段送出、36 段對齊、1941 字。但 21 段被合理性檢查攔下 |
| 3 | 加上單字時長下界的容差修正 | **53 段對齊、2481 字**，剩 4 段未對齊 |
| 4 | vLLM utilization 0.8 → 0.70 | 與第 3 次**逐字元完全相同**，僅字級時間戳位移至多 0.47 秒（見 2.4） |

第二次那 21 段揭露了一個比閾值更基本的缺陷：**下界設在 0.08 而時間戳相減帶浮點尾數，使同一種時長被判到兩邊**。1941 字中 392 個恰為一個解析度單位，216 個因此被誤判為異常，佔全部「異常」的 54.5%。改為對下界做容差比較後救回 17 段。細節見 `bff/src/vibe_vox/alignment.py` 的 `_DURATION_TOLERANCE_SECONDS` 與 #32。

剩下 4 段：2 段是 #40 的跨距判準誤判（「可以。」兩個字不可能佔滿 1.64 秒），2 段的零時長字佔四到五成，是模型真的退化、判準攔對了。

以下是三張票的根因記錄，留著是因為它們各自推翻了一個先前的判斷。

**#36（原本阻塞）**：63 個 Segment 全部送去對齊，超過 `MAX_BATCH_ITEMS = 32`，aligner 回 400 `BATCH_TOO_LARGE`，adapter 映射為 `AlignerUnavailable`，端點層降級成全段未對齊。

Log 證據：

```
aligner-1 | "POST /align HTTP/1.1" 400 Bad Request
bff-1     | 對齊服務不可用，全段降級為未對齊：AlignerUnavailable()
```

`audio_duration` 讀到 610.385 秒表示轉碼與 ASR 都成功，失敗點只在對齊。

**這推翻了 #27 的一個假設。** 不分批的理由原本寫在 `aligner/README.md`（該處已隨修正改寫）：「日常負載 2–4 段，一次送完即可」，那是單語者回合制對話的假設。多語者會議的切分完全不同：語者切換處必然切段，610 秒切出 63 段、平均 9.65 秒（ADR-0004 記載的是 30 至 40 秒），且有大量「是啊，是啊。」這類 0.77 至 1.6 秒的應答段。

同一份 README 當時還寫著「若真遇到超過 32 段的輸入，分批的代價也很小」。**知道會發生、也判斷了代價很小，但沒有實作。** 而 aligner 的錯誤訊息本身就是「請分批送」，呼叫端卻沒有那個能力。

已修：`align()` 依設定分批，並維持批次級故障隔離，某批失敗不丟棄其他批的結果。上限實測後定為 8（57 段切成 8 批），**不要調高**，理由見 2.3。

上限成為跨元件耦合（兩邊都讀同一個 env var，操作注意事項見 2.3），故 `test_config.py` 會實際去讀 aligner 的 config 與 compose 檔比對，不靠註解。過程中發現 aligner 服務原本連 `environment` 區塊都沒有，設那個變數對它從來不生效，屬 #35 那類「設定看得到卻不生效」。

另有兩個缺陷是審查抓出來的，都不在原票範圍內：batch size 設成 0 或負數時分批會拋 `ValueError` 而端點只攔對齊服務的兩種例外，故每個請求都回 500、逐字稿一併失效（已改為 `Settings` 啟動時擋下）；全批失敗且原因不同時只 raise 第一個會讓其他原因完全消失（現在其餘批次的原因會記進 log）。

**#37（可觀測性）**：`AlignerUnavailable()` 空括號，看不出是 400 還是 503。aligner 回的 `{"error":{"code":"BATCH_TOO_LARGE","message":"單次 63 段超過上限 32 段，請分批送。"}}` 被 `raise AlignerUnavailable from exc` 整份丟棄。**現成的答案被丟掉，然後花力氣從「63 段」反推出來。** 另一半是整批失敗時逐段記了 63 條完全相同的 `empty_words`，把真正的訊息推出畫面。

**#38（資料正確性，目前碰巧未發作）**：VibeVoice 對非語音區段輸出方括號標記，本次有六段（`[Silence]` × 3、`[Unintelligible Speech]` × 2、`[Music]`），`Speaker` 為空。它們的 Content 非空，所以會被送去對齊，而 `clean_token` 剝掉方括號後 `Silence` 會成為一個 Word，也就是一個假的字級時間戳。

這次它們被判準攔下（假 Word 時長超過 2 秒上界），但那是巧合：若靜音段較短，時長落在 `[0.08, 2.0]` 內就會通過。**而本次的第一段正是 `[Silence]`（0 至 2.45 秒）**，它若通過，`speech_start` 會變成 0，等於宣稱沒有開頭沉默，而那 2.45 秒本身就是開頭沉默。這與 #28 極力避免的假資訊同類，只是來源不同。

已修：`_is_alignable` 排除「整段只有一個方括號標記」的 Content（`^\s*\[[^\]]*\]\s*$`）。不用「`Speaker` 為空」作判準，因為空語者只是伴隨現象。

**#39 留給你判斷，不是技術問題。** `transcription_only` 是所有 Segment 的 `Content` 串接，故那六段標記會讓「Silence」這類英文詞出現在中文逐字稿裡。後端目前不過濾，因為那取決於 AI_practise 怎麼用這個欄位：餵給評分模型時「這裡有 16 秒聽不清」是有用訊號，直接呈現給學員看則是雜訊。行為與過濾用的 regex 已寫進 `docs/api/asr.md` §6，消費端至少不會被絆到，沒有立即損害。

### 4.2 TTS 引擎變更（#13 map + #14–#20）

**2026-08-05 已解三張：#14、#15（research，皆已關閉）、#16（決策）。** 剩 #17 #18 #19 #20 四張決策票，加下游的 #6–#8。

**#13 那張 map 不需要 re-scope。** 它的 Notes 第一行即「TTS 引擎已於 map #9 定案由 Qwen3-TTS 改 VoxCPM2」，並記著兩道 spike（#11 #12）皆 PASS 與 charting 時 grill 定的骨架（Preset speaker 重定義為系統預建的唯讀 Voice）。**T1–T7 是知道 VoxCPM2 之後才畫的**。決策 index 在該 map 的 `Decisions so far`，**手動維護**——因為 `/wayfinder` 沒裝在目前的 plugin 版本（mattpocock-skills 1.2.0 只出 diagnosing-bugs、tdd、prototype、research、domain-modeling、codebase-design、code-review、resolving-merge-conflicts、grilling）。research 票直接走 `/research`、決策票走 `/grilling` 與 `/domain-modeling`。

**已定的三件事**（完整記錄見 #13 的 index 與兩份 findings）：

1. **傳輸採 vLLM-Omni 的 `/v1/audio/speech`**（#14）。最咬人的發現是 `instructions` 欄位對 VoxCPM2 **從未被讀取且不報錯**——帶了會回 200 加一段沒有情緒的音訊。風格的唯一通道是把 `(...)` 寫進 `input`。
2. **TTS 文字前處理層走開源路徑，放棄 ttsfrd**（#15，使用者裁決）。詳見下方。
3. **合成一律固定 Controllable 模式，不暴露模式選擇**（#16，使用者裁決）。兩型音色因此一律吃 Instruction，能力感知的「停用欄位」規則整條消失。`ref_text` 降為管理用 metadata，**不得進入合成路徑**——送了會讓 Instruction 靜默失效。

**「系統預建 Voice」這個型別已被推翻，不要再用。** charting 時定的骨架是「Preset speaker 重定義為系統預建的唯讀 Voice」，但 VoxCPM2 沒有任何內建語者，所謂預建音色就是我方自己建的 clone 或 design——那是「誰建的」而非一種音色。Voice 現為兩型（clone、design），`type` 去掉 `'preset'`，User Story 44（唯讀音色保護）已刪除並重新編號。

**ttsfrd 是使用者指定的需求，但查證後判定為 blocker。** 使用者原話稱它為 Text Normalization Frontend；該展開未見於官方 artifact（wheel 的 METADATA 只寫 `tts frd engine for python module`）。兩個獨立成立的否決理由：**無任何授權可依循**（wheel METADATA 無 License 欄、ModelScope 授權欄位皆空字串、repo 無 LICENSE，且 `resource.zip` 內含明文禁商用的 Festival OALD 語料），以及 **`.so` 內沒有 ZhTW locale**（中文系只有 ZhCN／ZhHK／ZhSC／ZhSH，其中文路徑就是 zh-CN，即使取得授權對台灣仍是錯的工具）。兩項皆由本 session 獨立複驗。改走的開源路徑零額外授權面：VoxCPM2 內建正規化器本身就是 `wetext`（Apache-2.0，已在相依樹）。

**文件不一致已全部解除**：`CONTEXT.md`、`docs/spec.md`、`CLAUDE.md`、`docker-compose.yml`、ADR-0003 的 Qwen3-TTS 敘述全部改為 VoxCPM2；ADR-0001 標 `superseded`，不改內文，取代它的新 ADR 是 #19。全 repo 已無殘留（`.remember/` 的歷史紀錄除外）。

**消費端 TTS 契約已寫出**：`docs/api/tts.md`，供 AI_practise 撰寫 provider。它是**待實作的契約**，與 `asr.md`（已實作行為）性質不同，BFF 目前沒有任何 `/api/tts/*` 端點。`GET /api/tts/voices` 的回應形狀由 `{preset_voices, custom_voices}` 改為 `{voices:[{id,name,type,language}]}`，這對 AI_practise 是破壞性變更。

**#6–#8 已 re-scope**：三張都留了逐條的不成立項並**移除 `ready-for-agent` 標籤**（它們的規格與新決策衝突，照做會實作出錯的行為，#7 的能力規則是被反轉而非只是過期）。

**#31 的前置未解**：`HANDOFF.md` §8.2 原本把「實測 VoxCPM2 佔用」歸給 #14，那是誤植（#14 是 research／AFK 票，且 #13 的 Out of scope 排除實作）。它屬 #31，可執行的量測程序與起始參數值已寫進該票留言。

**既有資產**：spike harness 在分支 `spike/voxcpm-tts`（Docker、`optimize=False`、`{pinyin}` 鎖台灣破音字）。四份引擎評估在 `docs/superpowers/specs/`。

---

## 5. 給接手者的警告

### 5.1 驗證要從「既有狀態」出發，不是從乾淨狀態

本 session 兩次栽在同一個模式上，都是對抗性審查抓出來的：

**#28 的第三項判準原本是 no-op。** 我實作的是「word 時間戳須落在切片範圍內」，但 adapter 的時間戳恆為「切片相對時間 + 切片起點」，必然落在該範圍，永不觸發。而我的測試是手工造出 adapter 產不出的資料來驗證它，等於測試驗證了想像的行為。票要抓的「首末時間與音訊長度偏離」是跨距問題（40 秒段落的字全擠在前 3 秒），不是位置問題。

**#33 的驗證漏了唯一會實際發生的升級路徑。** 我測了三種情境（`--force-recreate`、`build` + `up`、`down` + `up`）全部從乾淨狀態起跑，漏了「舊容器帶著資料時套用修正」，而那是遠端唯一會發生的路徑，且結果是資料會丟。補測後才有了第 1 節那三條指令。

**共通模式**：我的驗證從理想的初始狀態出發，而真實情境永遠是「既有狀態 + 變更」。下次驗證任何變更前，先問「現場現在長什麼樣」。

### 5.2 推算的數字要明說是推算，而且它們會咬人

四個閾值**全部是推算而非實測**：切片 buffer 0.5 秒（#27）、單字時長上界 2.0 秒、跨距下界 0.5 倍、單字異常容許佔比 0.3（#28 與 #34）。可用樣本只有 #26 那 13 個字（官方測試音訊 4.204 秒），不足以定比例閾值。

這不只是記載問題，推算值真的造成了兩次故障：

**我一度在 ADR-0004 寫下**「採物理界限而非比例啟發式，據此定比例閾值會製造誤判」，然後加了正是那個東西，而它真的製造了誤判（誤判頭尾沉默段）。

**單字時長的零容忍讓字級對齊實質不可用**（#34）：9 段錄音只有 2 段通過。那個判準的界限本身是物理導出的（80 ms 解析度），但「零容忍」這個策略是我加的，沒有實測依據。

**別讓推算值看起來像實測值**，也別假設物理導出的界限配上任意的容忍策略仍然安全。ADR-0004 的判準表現在有「校準狀態」欄，每個值都標明是量的還是算的。

### 5.3 沒設定的預設值也是配置，而且沒人知道它在那裡

`frontend/nginx.conf` 從未設過 `proxy_read_timeout`，於是 nginx 的預設 60 秒成了整個系統音檔長度的實際上限（約 5 分鐘）。同時 BFF 的 guard 是 240 秒、`docs/api/asr.md` 宣稱 61 分鐘、前端警示寫 60 分鐘：**四個數字沒有一個對得上，而最小的那個從未被任何人選擇過**（#35）。

這類缺陷的特徵是：它不在任何人寫的程式碼裡，所以 code review 看不到；它只在超過某個規模的輸入才顯現，所以測試碰不到；而它的症狀（504）看起來像模型太慢或服務掛掉，會把診斷引向錯的方向。

**逾時、大小、併發這類邊界值，凡是跨越元件的都要明確寫出來並讓兩端互相對照。** `test_config.py` 現在有一條測試守住「BFF guard < nginx timeout」，那是這條教訓的具體化：它不驗證任何業務邏輯，只驗證兩個檔案的數字沒有走散。

### 5.4 現成的答案不要丟掉

診斷 #36 花的工夫本來不必要。**答案完整寫在 aligner 的回應體裡**（4.1 有原文），而當時 adapter 的 `raise AlignerUnavailable from exc` 把它整份丟棄，log 只印出 `AlignerUnavailable()`。於是我從「63 段」這個數字反推出結論，重新導出了系統早就知道的事。這件事本身已隨 #37 修掉，留下這條是因為模式會重演。

同一模式在本 session 出現三次：#35 是「nginx 的預設值沒人選擇過」、#37 是「錯誤內容被丟棄」、#36 是「服務端說了『請分批送』而呼叫端沒有那個能力」。**資訊在系統裡，只是沒被傳遞。**

跨元件的錯誤一律要攜帶上游的 code 與 message。包裝例外時若丟掉原始內容，下一個人就得靠反推，而反推不一定成功。

### 5.5 使用者的實測資料比我的推理可靠

#34 是使用者貼一份實際辨識結果才發現的。我在 #28 寫了 18 條測試、跑過對抗性審查兩輪，都沒抓到「長段落幾乎必然被攔」，因為我的測試樣本都是 2 到 3 個字的小段落，那個規模下累積機率問題不存在。

**測試資料的規模要貼近真實負載。** 實際段落是 30 到 40 秒、100 到 200 字；我測的是 2 到 3 字。這個落差讓一整類問題完全隱形。

### 5.6 不要用 shell 做批次文字替換，尤其是中文

本 session 用 PowerShell 的 `-replace` 批次清理文字，靜默破壞了兩個檔案：`docs/api/asr.md` 的「單」全被換成「一」（白名單→白名一、單聲道→一聲道，10 處），`bff/tests/test_alignment.py` 的「長」全被換成「段」（5 處）。

根因是 PowerShell 把單元素的嵌套陣列展平了：

```powershell
# 意圖：$p[0] 是要找的字串、$p[1] 是替換成的字串
$pairs = @{ 'file.md' = @( @('要找的長字串', '替換的長字串') ) }
foreach ($p in $pairs['file.md']) { $c = $c -replace $p[0], $p[1] }
```

只有一個 pair 時，`@( @('a','b') )` 被展平成 `@('a','b')`，於是 `$p` 是**字串**而非陣列，`$p[0]`／`$p[1]` 取到的是**該字串的第一、二個字元**。實際執行的是 `-replace '要', '替'`。多個 pair 的檔案沒事，只有單一 pair 的檔案中彈。

**破壞是靜默的**：測試全綠（只有註解與文件受影響）、typecheck 通過、diff 太大而肉眼掃不出來。是 `git diff` 的 system-reminder 顯示「白名一」才發現。

用 Edit 工具做替換。它要求精準匹配、一次一處、匹配不到就報錯，沒有這類靜默失敗的空間。若非得用 shell，替換後必須 grep 驗證，不能只看測試通過。

### 5.7 測試可能假通過

#29 有一條測試（重新辨識後展開狀態重置）第一版是假綠的：它用 `waitFor` 等 `queryByText` 變 null，而 `submit()` 的 `setResult(null)` 會讓整個結果區塊短暫消失 → `waitFor` 立刻通過，新結果渲染回來時展開狀態其實還在。

改法是讓第二次的 mock 回不同 `Content`，先 `await screen.findByText("第二次辨識")` 確認新結果已渲染，再斷言。**斷言前要確認被測的新狀態真的到位了**，否則抓到的是中間態。

### 5.8 能在本機驗證的就不要推出去讓使用者當 CI

這條是前一個 session 的教訓（第一版 aligner Dockerfile 未經 build 就 push，讓使用者在遠端連撞兩層失敗）。本 session 已遵守：#33 的部署變更在本機用 Docker 完整驗證（含模擬舊版容器、實測救援指令）。

`docker build` 不需要 GPU，只有 `docker run` 需要真實推論才需要。而連真實對齊都能在本機驗：**設 `VIBE_VOX_ALIGNER_DEVICE=cpu` 即可跑完整推論**，輸出與 GPU 逐字相同。

### 5.9 技術上限不等於設計情境

前一個 session 曾以「61 分鐘音檔約 100 段」推導出「必須分批」、「VRAM 一定不夠」等結論並寫進文件。但 61 分鐘是 `docs/api/asr.md` 記載的**技術上限**，實際資料平面是回合制對話、單輪 1 至 2 分鐘、約 2 至 4 段。修正後峰值從外推的 13094 MiB 降為實測 2728 MiB，結論完全相反。

看到規格裡的極限值時，先確認實際負載長什麼樣。

---

## 6. 資源位置

`D:\pro\VibeVoice-ASR`（使用者另一專案，同一台遠端 GPU，辨識率良好）：

- `server/app.py` — 實證有效的 vLLM 呼叫配置
- `VibeVoice-main/vibevoice/processor/vibevoice_asr_processor.py:360` — **訓練時實際使用的 prompt 組裝**，權威度高於 test 與 demo
- `VibeVoice-main/finetuning-asr/lora_finetune.py:250-257` — `_format_transcription`，模型輸出 JSON key 的訓練目標格式
- `VibeVoice-main/finetuning-asr/toy_dataset/` — 訓練標註樣本，時間戳語義的權威來源
- `VibeVoice-main/vllm_plugin/inputs.py` — 音訊前處理，寫死 resample 至 24000、上限 61 分鐘
- `VibeVoice-main/vllm_plugin/scripts/start_server.py` — **vLLM 的預設參數**：`gpu_memory_utilization=0.8`、`max_model_len=65536`、`max_num_seqs=64`，即 #31 的根據。行號 90-92 是依 GitHub 上的 `microsoft/VibeVoice`；本機 clone 若版本不同，搜尋 `build_vllm_command`

`D:\pro\AI_practise` — 消費端專案。錄音實作在 `web/src/features/practice/audio/recorder.ts`（16 kHz）。

**本 repo 的權威文件**：

- `docs/api/asr.md` — 消費端契約，**全文皆為現行行為**（〔規劃〕標記已於 #28 全部落地）
- `docs/adr/0004-word-level-forced-alignment.md` — 字級對齊的決策、GPU 實測數據、四項判準與各值的校準狀態
- `aligner/README.md` — aligner 服務的契約與對上游 `qwen-asr` 的查證事實
- `CONTEXT.md` — 領域詞彙（Segment、Word、Forced alignment、對齊狀態）

**aligner 的上游**：`QwenLM/Qwen3-ASR`（GitHub）。關鍵檔案 `qwen_asr/inference/qwen3_forced_aligner.py`（`align()` 的完整實作與 `fix_timestamp`）、`qwen_asr/inference/utils.py`（`MAX_FORCE_ALIGN_INPUT_SECONDS`、音訊正規化）、`qwen_asr/core/transformers_backend/modeling_qwen3_asr.py`（音訊編碼器的分塊處理）。用 `gh api repos/QwenLM/Qwen3-ASR/contents/<path>` 讀。

### 6.1 prompt 的兩套 keys 不是矛盾

- **prompt 裡的欄位描述**：`Start time, End time, Speaker ID, Content`（processor、test_api.py、gemini 實作）
- **模型輸出的 JSON key**：`Start, End, Speaker, Content`（`_format_transcription` 的訓練目標）

官方刻意如此。本 repo 現已對齊 processor，且解析端也加了 fallback。

---

## 7. 環境與部署

- **架構**：docker compose 五部署單元，即 bff（FastAPI）、frontend（nginx，對外 **8088**）、vllm（VibeVoice-ASR，GPU）、aligner（Qwen3-ForcedAligner，GPU，埠 9100）、tts（profile，未啟用）
- **遠端 GPU 機**：`http://10.2.66.102:8088`
- **rebuild**：`git pull && docker compose build <服務> && docker compose up -d <服務>`。只 build 需要的服務，vllm image 裡 bake 了 7B 權重，沒必要跟著重建
- **改過 nginx.conf 就必須 build frontend**：它打包在 frontend image 裡，只 build bff 不會生效。#35 的修正涉及兩者，故當時的部署是 `docker compose build frontend bff`
- **資料庫在 volume `bff_data`（掛到 bff 的 `/data`）**，故 rebuild 不再清空 Hotword。首次部署此修正前務必先匯出，見第 1 節
- **本機（Windows）**：可跑 stub e2e、`docker build`（不需 GPU）、`VIBE_VOX_ALIGNER_DEVICE=cpu` 的完整對齊驗證，以及用 `docker-compose.dev.yml` 起 bff 做真實的容器行為驗證
- **模型權重**：`microsoft/VibeVoice-ASR`（非 -HF）bake 在 `docker/vllm.Dockerfile`，served-name `vibevoice`；`Qwen/Qwen3-ForcedAligner-0.6B` bake 在 `docker/aligner.Dockerfile`

### 7.1 aligner 刻意不列入 bff 的 `depends_on`

aligner 不可用時 ASR 逐字稿仍須照常回傳（ADR-0004 的第二層降級），硬依賴會讓那層降級失效。`docker-compose.yml` 裡有註解記著，別「順手補上」。

### 7.2 會刪掉 Hotword 的三種操作

- **`docker compose down -v`**：`-v` 就是刪 volume 的意思。日常停服務用 `down` 或 `stop`，不要加 `-v`
- **`docker volume prune`／`docker system prune --volumes`**：容器已停或已移除時，這兩個會把 volume 當成無主的一併清掉。這台機器的 image bake 了 7B 權重、磁碟壓力大，清理是高機率動作，執行前先確認 bff 容器還在跑
- **搬動 repo 目錄或設 `COMPOSE_PROJECT_NAME`**：volume 實際名稱是 `<project>_bff_data`，project 名預設取自目錄名，換名等於指向另一個空 volume

備份一律走 `GET /api/admin/hotwords/export`，不要試圖直接複製 volume 裡的 SQLite 檔（WAL 模式下有 `-wal`／`-shm` 側檔，單獨複製主檔可能拿到不完整的狀態）。

**若 bff 起不來且 log 出現 `Permission denied`**：多半是 volume 早於本次 image 就存在（曾手動 `docker volume create`，或曾以無 `/data` 的舊 image 掛過同名 volume）。此時該 volume 的 owner 已定為 root，Dockerfile 的 `chown` 不會再被繼承，需先 `docker compose down && docker volume rm <project>_bff_data` 再起（該 volume 若已有資料，先按第 1 節匯出）。

### 7.3 改名的殘留風險

環境變數前綴在 `da08334` 全面改為 `VIBE_VOX_`，**不保留舊前綴的相容 alias**。遠端若有自建 `.env`，裡面的 `VIBE_QWEN_*` 需改名，否則設定失效並退回預設值。Python package 亦由 `vibe_qwen` 改為 `vibe_vox`。日後若從舊備份還原 `.env`，這條會再咬一次。

### 7.4 GPU 拓撲：兩張卡，不是一張

**這推翻了 ADR-0001 與 ADR-0004 共用的前提**，兩份都寫「單張 RTX 6000 Ada 48 GB」。ADR-0004 已回填實測，ADR-0001 未改（見 8.3）。實測：

| 卡 | 容量 | 佔用 | 佔用者 |
|---|---|---|---|
| GPU 0 | 46068 MiB | 40618 MiB | Vibe-Vox 的 vllm（37890）+ aligner（2728，日常負載） |
| GPU 1 | 46068 MiB | 33118 MiB | gpustack 管理的 `qwen3.6-35b-a3b`、`gemma-4-12b-it-qat` |

三點要注意：容量是 46068 MiB 而非 48 GB 的標稱值；GPU 1 被**非 Vibe-Vox 的工作負載**佔著且由 gpustack 動態調度，其餘裕不可假設穩定；`docker-compose.yml` 目前把三個 GPU 服務全釘在 `device_ids: ["0"]`。

**測試環境定位**：不必考慮多併發，但 prod 確定會是多併發架構。服務須無狀態、不用全域可變狀態、不假設獨佔 GPU，屆時加 replica 即可，不得需要重寫。

---

## 8. 其他待辦

### 8.1 #32：四個推算值待校準（單字時長下界已完成）

| 閾值 | 目前值 | 狀態 |
|---|---|---|
| 單字時長下界 | 0.08 秒加 1e-6 容差 | **已完成**。由模型的時間解析度導出，容差的必要性已實測證明 |
| 單字時長上界 | 2.0 秒 | 推算值。**2481 字中從未觸發**，超過上界者 0 個 |
| 異常容許佔比 | 0.3 | 推算值。實測中位數 9.7%，但通過段落最高 28.6%，**餘裕只有 1.4 個百分點** |
| 跨距下界 | 0.5 倍 | 推算值，且有已知誤判（#40） |
| 切片 buffer | 0.5 秒 | 推算值。漂移量**無法從 API 回應量測**，見下 |

會議錄音已提供第一份真人樣本（53 段、2481 字，資料在 #32）。**還需要第二份，而它最好是練習錄音**：跨距判準對首末段的行為只有含刻意開頭沉默的錄音能驗，而會議錄音通常已在進行中才開始錄。**不必等接上 AI_practise**，理由與判別方法見 8.1.1。

**一個已知的量測缺口**：切點漂移量拿不到。`alignment.py` 的 `_with_alignment` 在 `aligned` 時把 Segment 的 `Start`／`End` 覆寫成首末字的邊界，故回應裡段界與字界恆等，量出來永遠是 0。要量它必須取得合併前的原始 ASR 段界，那不在消費端契約裡。

要收集四項：切點漂移量（比對 `Segment.Start`／`End` 與該段首字／末字）、單字時長分布、跨距比分布、被攔下段落的逐一確認（最有價值，直接指出閾值該往哪動）。不需 GPU，`VIBE_VOX_ALIGNER_DEVICE=cpu` 即可。

**#34 之後這件事容易許多**：失敗原因現在會寫進 log，不必再從 `aligned: false` 反推。第一步就是拿現有錄音重跑，讀 log 統計各判準的攔下次數。

**#36 修好後這條路才通**：在那之前，手上唯一的真人樣本（10 分鐘會議錄音）因 63 段超過 batch 上限而完全無法對齊，校準拿不到任何資料。程式已在 main，重跑一次就能開始收資料。

#### 8.1.1 樣本適用性：哪種錄音能校準哪個閾值

**TTS 產出的音檔不能用來校準這些閾值。** 使用者提供的第一份資料（214 秒、9 段）是 TTS 合成語音，特徵是：完全數位靜音（RMS 恰為 0 的窗）、無環境底噪、頭尾靜音各僅 0.02 秒、語速由模型控制而極穩定（單字時長 p5 0.160、p95 0.320，範圍很窄）。

用它校準的風險是把閾值收緊到「TTS 的規律性」上，一遇到真人錄音就大量誤判，那是 #34 的錯誤重演一次。

判別方法（本 session 實測有效）：算每 20 ms 窗的 RMS，看最低 5% 窗的相對能量。真人錄音的環境底噪在 0.001 至 0.02 之間，合成語音接近 0；另看是否有 RMS 恰為 0 的窗，真人錄音不會有。

**會議錄音能校準三項，不能校準第四項。** 單字時長上界、異常佔比、切點漂移都適用（它們防的是人聲的不規則）。**首末段的跨距判準不適用**：那項的關鍵是頭尾沉默的分布，而會議通常已在進行中才開始錄，與「學員按下錄音後不敢開口那 20 秒」性質完全不同。那一項要等真的練習錄音。

**練習錄音不必等接上 AI_practise。** 關鍵不在「經過 AI_practise 的程式碼」，而在「有人真的對著麥克風練習話術，包含不敢開口的沉默與卡頓」。用管理平面上傳一段自己錄的、刻意含開頭沉默 20 秒與中間忘詞的音檔，價值與經過 AI_practise 相同：ADR-0004 要防的異常來自人聲的不規則，不來自傳輸路徑。而 `recorder.ts` 的 16 kHz 差異，ADR-0004 已記載為「該來源改與不改等價」。

**有一件事只有使用者能做**：被攔下的段落要聽該段音訊，判斷是真對歪還是誤判。那是校準的最後一步，也最有價值，它直接決定閾值該往哪個方向動。

該票另記一個缺口：跨距判準因會誤判頭尾沉默而排除首末段，故首末段的擠壓型對歪目前抓不到，收斂需要 VAD 或實測的頭尾沉默分布。

### 8.2 #31：三個模型能否共存於這張卡

**已量到的兩項**（2026-08-05，帶 `gpu_uuid`、vLLM 完全啟動後）：

| 行程 | MiB |
|---|---|
| vLLM（0.70／24576／8） | **33654** |
| aligner（處理過請求後） | **3620** |

**TTS 裝不裝得下現在算不出來，別再算了。** 三個原因：

1. **VoxCPM2 的需求（約 8192 MiB）是估算而非實測**，而它是分子。
2. **aligner 的佔用會長。** idle 時 2186、處理過請求後 3620，PyTorch 的 caching allocator 不還回去（見 2.2），穩態上限未知。要降只能 `docker compose restart aligner`。
3. **GPU 0 的總量本身有兩個值**：`nvidia-smi` 記 46068、torch 報 45465（2.2 的表以後者為實際）。兩者差 603 MiB，而這個差已經大於任何算得出來的餘裕，所以精確到百 MiB 的餘裕計算是假精度。

**要答案就實作再量，不要繼續推算。** 順序：把 VoxCPM2 服務化並量它的實際佔用 → 同時量 aligner 在 batch 8 之下跑完長音檔的穩態佔用 → 三個數字擺在一起才知道夠不夠。

**這件事屬 #31，不屬 #14**（本節原本寫「#14 把 VoxCPM2 服務化並量」，那是誤植）：#14 是 research／AFK 票，而 #13 的 Out of scope 明訂該 map 只到決策與文件層。#14 已完成的是傳輸選型，它替本項留下了可執行的量測程序與起始參數值，已寫進 #31 的留言。**官方的三個記憶體數字沒有一個能拿來規劃本機容量**（模型卡約 8 GB、recipe 約 22 GiB、現行 deploy yaml 自述 peak 約 13 GiB，recipe 那組對應較舊的 yaml 已過期），這是不要再算的第四個理由。

**若量完發現仍不夠，剩下的槓桿依代價由小到大**：再降 utilization（下限由「KV cache 須裝得下一條 `max-model-len` 序列」決定，每 token 約 56 KiB）、降 `max-model-len`（會使長音檔改為立刻回 400 而非等到逾時，須同步 `docs/api/asr.md` §3.3 與 `frontend/src/asr.ts`）、`kv-cache-dtype fp8`（動數值精度，**必須做逐字稿的逐字元 diff**，不能只確認服務起得來）。

### 8.3 ADR-0001 需重寫

它的整套 VRAM 協調論述建立在「單張卡」之上（見 7.4），且其 `gpu_memory_utilization` 0.55 至 0.6 的假設**長期從未被實作**（實際跑在上游預設 0.8），2026-08-05 才第一次被顯式設定並調至 0.70（見 2.4）。#19 是「新 ADR 取代 ADR-0001」的票，該票須納入兩張卡、第二張被別的專案動態佔用、以及三個記憶體參數的實際值這三項事實。

**這個衝突目前是明知而未解的**：`docs/agents/domain.md` 要求與既有 ADR 衝突時明確標示而非默默覆蓋，故 2.4 與 ADR-0004 都已註明 ADR-0001 仍寫著 0.55 至 0.6。真正的修正在 #19。

### 8.4 payload 放大

轉碼後 wav 以整檔 base64 進 JSON 送 vLLM，60 分鐘音檔請求體約 230 MB。既有架構問題，而依實際負載（1 至 2 分鐘）無虞。

**注意這與 BFF 的 200 MB 上傳上限是兩件事**：那條管的是使用者上傳進來的音檔，這條是 BFF 送出給 vLLM 的請求體，兩者沒有共用的限制。實務上目前碰不到，因為 #35 之後音檔的建議上限是約 20 分鐘（受辨識逾時限制），對應的出站 payload 約 80 MB。若日後提高逾時以支援更長音檔，這條要一併處理：改檔案路徑或分塊傳輸，**不要退回低取樣率**。

### 8.5 aligner 容器以 root 執行

與 vllm 容器一致，且 `HF_HOME` 下的權重層若 chown 會整份複製一次。代價寫在 Dockerfile 註解裡：本服務以 libsndfile 解碼外部來源的音訊，而 libsndfile 有緩衝區溢位的 CVE 史。目前的緩解是該容器不對外且輸入已先經 ffmpeg 正規化。要收斂就建非 root 使用者並以其身分執行 `snapshot_download`，需重新 build 驗證。

**注意這個緩解的前提**：它建立在「aligner 不對外」之上。若日後為了 debug 在 compose 裡把 9100 映射出去，整段緩解即失效，屆時應先處理非 root，或至少限制映射的來源位址。

注意 bff 容器已是非 root（uid 1001），與 aligner 不同，這是 #33 的 volume owner 陷阱的來源。
