# Vibe-Vox 交接文件

**日期**：2026-08-04
**分支**：main，與 `origin/main` 同步於 `b8333e0`（#34 的修正尚未 merge，見第 3 節）
**範圍**：字級強制對齊 T2–T4 落地（#27 #28 #29，該條線全數完成）、Hotword 持久化缺陷修正（#33）、對齊判準過嚴的修正（#34）

---

## 0. 一句話現況

**字級強制對齊從選型到落地全部走完**（#25 map 與 #26–#29 四張子票均已關閉），消費端契約 `docs/api/asr.md` 的〔規劃〕欄位全部生效。期間修掉兩個會讓功能實質不可用的缺陷：Hotword 每次 rebuild 就消失（#33），以及合理性檢查對長段落過嚴、9 段錄音只有 2 段通過對齊（#34，修正已驗證對該案例有效，但需部署後以 log 確認其餘段落）。

**優先度最高的下一步是 TTS 引擎變更**（#13 map + #14–#20）：它同時解掉三份文件與已定案決策的不一致、解封 #6–#8、也讓 #31 有辦法往下走。另一個可獨立進行的方向是 #32（閾值校準），但它卡在資料而非程式碼：需要真實學員錄音。

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

## 2. 三條不要再動的線

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

### 2.2 aligner 的 VRAM：用實測值，不要退回估算

ADR-0004 原本寫 3 至 4 GB，那是估算。實測已取代它，且比估算省：

| 項目 | 實測值 |
|---|---|
| 權重載入後 idle | 2186 MiB |
| 單段對齊（4.2 秒）峰值 | 2348 MiB（idle 加 162） |
| 32 段（34 秒段長）累積 | 5750 MiB |
| 日常負載 4 段累積 | 2728 MiB |

**注意這是累積量測**：同一容器內由小而大依序執行，而 PyTorch 的 caching allocator 不釋放已配置的記憶體，故各級數字是該級的**上界**而非獨立峰值。判斷安全上限時方向保守，但不可拿來做精確容量規劃。要獨立峰值需每級之間重啟容器。

量測腳本在 `aligner/scripts/bench_vram.sh`，**在 GPU 宿主上執行**（不是容器內，理由見腳本註解）。**vLLM 的配置一改就要重測。**

重測有一個會咬人的副作用：跑完 32 段後 aligner 的佔用漲到 5750 MiB 且**不會自己降回**，GPU 0 的餘裕從 5450 變成 2428。此時若試著啟動 tts 會撞到看似毫無道理的 OOM。要把記憶體還給同卡的其他服務，需 `docker compose restart aligner`。

### 2.3 `VIBE_VOX_ALIGNER_MAX_BATCH_ITEMS = 32`：別急著調

它的角色是異常防護，不是日常限制。日常負載 2 至 4 段，觸不到它。降到 8 在實際負載下沒有任何收益，因為**日常負載的峰值**本來就只有 2728 MiB（32 段那個 5750 只在異常輸入時才會發生）。

---

## 3. 本 session 完成

新→舊：

| 位置 | 票 | 內容 |
|---|---|---|
| 分支 `fix/34-lenient-word-duration` | #34 | 判準分兩層，單字時長異常不再使整段失敗 |
| `b8333e0` | #33 | Hotword 資料庫改存 volume |
| `84645e7` | #29 | ASR 測試頁顯示對齊狀態與字級時間戳 |
| `d54d665` | #28 | 合理性檢查、兩層降級、消費端契約擴充 |
| `04d52ac` | #27 | aligner adapter：逐段切片、batch、offset 拼接 |

**#34 尚未 merge**，在分支 `fix/34-lenient-word-duration` 上；其餘皆已 push。

**測試**：`bff` 126 passed／4 skipped、`aligner` 20 passed、`frontend` typecheck + 33 passed + production build。

期間開了三張票：**#32**（四個推算閾值待校準）、**#33**（已修並關閉）、**#34**（已修，待 merge）。

### 3.1 #27 aligner adapter

`bff/src/vibe_vox/adapters/aligner.py` 的 `HttpAlignerClient` 與 `bff/src/vibe_vox/audio/slice.py`。

**切片不起 ffmpeg**，改用 stdlib `wave` 做 byte 層切片：輸入恆為 `AudioIntake.transcoded()` 的 pcm_s16le wav，格式是內部不變量，切片不需重新編碼，而每段 spawn 一個子進程要付逾時處理與檔案落地的代價。副效果是「全程不落暫存檔」自動滿足。

**切片 buffer 預設 0.5 秒**，左右各留以吸收 VibeVoice 切點漂移（段界是模型自選切點，可能落在某個字的發音中間）。該值是推算而非實測，見 5.2。

**故障隔離**：服務端對整個 batch 是全有全無，任一筆不合契約即整批回錯。空 `Content`（模型缺欄位時補空字串，`docs/api/asr.md` §6）會換來 400、零長度切片使推論失敗回 500，兩者都會毀掉同批正常段落。故退化段落在送出前剔除、結果中留空位。

### 3.2 #28 合理性檢查與兩層降級

`bff/src/vibe_vox/alignment.py`。**判準分兩層**（#28 原本沒有這個分層，是 #34 的實測逼出來的，見 3.5）：

| 型態 | 判準 | 層級 |
|---|---|---|
| 單字時長異常 | 落在 `[0.08, 2.0]` 秒之外 | **局部**：只累計佔比，單獨出現不使整段失敗 |
| 單字時長異常佔比過高 | 超過三成的字落在界外 | 結構：那不是雜訊而是系統性對歪 |
| 時間戳逆轉 | 後字 `Start` 早於前字 `End` | 結構 |
| 時間戳脫勾 | 落在該段切片涵蓋的範圍外 | 結構（防禦性下界，正常路徑不觸發） |
| 擠壓型對歪 | 對齊跨距不足段落切點跨距的一半 | 結構，**僅適用於既非首段也非末段的段落** |
| 零長度段落 | End <= Start | 結構 |
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

### 3.5 #34 判準過嚴：實測推翻了 #28 的一項設計

**使用者的實測資料直接推翻了零容忍的單字時長判準。** 保險話術錄音、語音乾淨、辨識品質良好、語速正常，9 個 Segment 中只有 2 個通過對齊檢查：

| 段落 | 段長 | 字數 | 結果 |
|---|---|---|---|
| 139.05–149.77 | 10.72 秒 | 42 | 已對齊 |
| 166.93–175.65 | 8.72 秒 | 33 | 已對齊 |
| 其餘 7 段 | 5.94–39.59 秒 | 最長約 190 | 全部未對齊 |

**通過的兩段都是短段。** 這是累積機率問題：單字異常率若為 1%，190 字的段落至少出現一個異常的機率是 85%，40 字只有 34%。首段與末段不套跨距判準，故它們的失敗只能來自單字時長判準，而第一段約 190 字、39.59 秒，平均每字 0.208 秒，語速完全正常。

根因是把常態雜訊當成故障訊號：#26 那個 13 字樣本裡就有一個零時長（「幾」），異常率 7.7%，而它在 `fix_timestamp` 之後仍出現，屬模型的常態輸出。零容忍讓「單字異常污染整段」，與 ADR-0004「單段對歪不污染其他段」的意圖相反，也使字級對齊實質不可用（評分端拿不到任何停頓資料）。

修法（使用者選定寬鬆語義）：判準分兩層，見 3.2 的表。**單字時長異常只在佔比超過三成時才使整段失敗。**

同時把 `is_sane` 改為 `find_defect`，回傳缺陷描述而非布林並寫進 log：

```
第 3 段未通過對齊檢查（implausible_duration_ratio）：單字時長異常佔比 45%（9／20 字）超過上限 30%，例如「幾」0.00 秒、「乎」0.01 秒
```

原本 `aligned: false` 不說明為什麼，無從判斷是模型對歪還是判準過嚴。這也是 #32 校準要收集的資料。

**接手者要做的驗證**：#34 merge 並部署後，用同一份錄音重跑，預期原本未對齊的段落多數會通過。**若仍大量未對齊，log 會指出是哪個判準**，那比推測可靠。我的診斷（失敗來自單字時長判準）是從「首末段不套跨距 + 語速正常」推論的，沒有直接證據，log 才會給出證據。

---

## 4. 下一步：TTS 引擎變更（#13 map + #14–#20）

**這是最大的未動工項目。** 七張子票（#14–#20）加下游的 #6–#8，共十張，全未動。

**repo 與決策不一致還擺著**：VoxCPM2 取代 Qwen3-TTS 已定案，但 `CONTEXT.md`、`docs/spec.md`、ADR-0001 三份文件至今仍寫 Qwen3-TTS。任何人現在讀 repo 都會拿到錯的選型。

依相依關係，接這條線同時解掉三件事：文件不一致、#6–#8 的封鎖、以及 #31 的前置（#31 第一步是實測 VoxCPM2 的實際佔用，那正是 #14 的產出）。

**開工方式**：`#14`（VoxCPM2 服務化與傳輸端點契約）與 `#15`（TW 破音字→拼音資源與 G2P 策略盤點）是兩張 research 票，可平行。另一條路是先重新盤 #13 那張 map 的範圍：它的子票是在 VoxCPM2 定案前寫的，可能需要 re-scope（#13 由 wayfinder 流程產生，若該 skill 仍可用即可沿用同一流程）。

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

### 5.3 使用者的實測資料比我的推理可靠

#34 是使用者貼一份實際辨識結果才發現的。我在 #28 寫了 18 條測試、跑過對抗性審查兩輪，都沒抓到「長段落幾乎必然被攔」，因為我的測試樣本都是 2 到 3 個字的小段落，那個規模下累積機率問題不存在。

**測試資料的規模要貼近真實負載。** 實際段落是 30 到 40 秒、100 到 200 字；我測的是 2 到 3 字。這個落差讓一整類問題完全隱形。

### 5.4 不要用 shell 做批次文字替換，尤其是中文

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

### 5.5 測試可能假通過

#29 有一條測試（重新辨識後展開狀態重置）第一版是假綠的：它用 `waitFor` 等 `queryByText` 變 null，而 `submit()` 的 `setResult(null)` 會讓整個結果區塊短暫消失 → `waitFor` 立刻通過，新結果渲染回來時展開狀態其實還在。

改法是讓第二次的 mock 回不同 `Content`，先 `await screen.findByText("第二次辨識")` 確認新結果已渲染，再斷言。**斷言前要確認被測的新狀態真的到位了**，否則抓到的是中間態。

### 5.6 能在本機驗證的就不要推出去讓使用者當 CI

這條是前一個 session 的教訓（第一版 aligner Dockerfile 未經 build 就 push，讓使用者在遠端連撞兩層失敗）。本 session 已遵守：#33 的部署變更在本機用 Docker 完整驗證（含模擬舊版容器、實測救援指令）。

`docker build` 不需要 GPU，只有 `docker run` 需要真實推論才需要。而連真實對齊都能在本機驗：**設 `VIBE_VOX_ALIGNER_DEVICE=cpu` 即可跑完整推論**，輸出與 GPU 逐字相同。

### 5.7 技術上限不等於設計情境

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

### 8.1 #32：四個推算閾值待校準

切片 buffer 0.5 秒、單字時長上界 2.0 秒、跨距下界 0.5 倍、單字異常容許佔比 0.3，**全部是推算值**。校準需要真實學員錄音跑完整鏈路（ASR → 切片 → 對齊 → 合理性檢查），倉庫內沒有這種資料，**這是資料缺口而非程式碼缺口**，若 AI_practise 那邊有樣本就能動。

要收集四項：切點漂移量（比對 `Segment.Start`／`End` 與該段首字／末字）、單字時長分布、跨距比分布、被攔下段落的逐一確認（最有價值，直接指出閾值該往哪動）。不需 GPU，`VIBE_VOX_ALIGNER_DEVICE=cpu` 即可。

**#34 之後這件事容易許多**：失敗原因現在會寫進 log，不必再從 `aligned: false` 反推。第一步就是拿現有錄音重跑，讀 log 統計各判準的攔下次數。

該票另記一個缺口：跨距判準因會誤判頭尾沉默而排除首末段，故首末段的擠壓型對歪目前抓不到，收斂需要 VAD 或實測的頭尾沉默分布。

### 8.2 #31：vLLM 的記憶體參數從未設定

`docker/vllm.Dockerfile` 直接跑官方 `start_server.py`，未覆寫任何記憶體參數，故跑在上游預設 `--gpu-memory-utilization 0.8`、`--max-model-len 65536`、`--max-num-seqs 64`。而 ADR-0001／ADR-0004 一直假設 utilization 是 0.55 至 0.6。

GPU 0 日常負載下餘 5450 MiB。ADR 記 VoxCPM2 約需 8 GiB，但**那是估算值而非實測**，與餘裕同一量級，所以「放不下」目前是推測而非結論。**第一步應是實測 VoxCPM2 的實際佔用**：那正是 #14 的產出，故本票卡在 TTS 線後面，不宜單獨動。

四個方向記在票裡，前三者機制不同可疊加：降 utilization 減的是總量上限，降 `max-model-len` 與 `kv-cache-dtype fp8` 減的是每個 sequence 的佔用（同樣的池能容納更多併發）。`max-model-len` 對 1 至 2 分鐘的對話大幅過剩，該從 vLLM log 取實際 prompt token 數後決定。fp8 動的是數值精度，必須做逐字稿的逐字元 diff，不能只確認服務起得來。

### 8.3 ADR-0001 需重寫

它的整套 VRAM 協調論述建立在「單張卡」之上（見 7.4），且其 `gpu_memory_utilization` 0.55 至 0.6 的假設從未被實作。#19 是「新 ADR 取代 ADR-0001」的票，該票須納入兩張卡、第二張被別的專案動態佔用、以及 utilization 實際為 0.8 這三項事實。

### 8.4 payload 放大

轉碼後 wav 以整檔 base64 進 JSON 送 vLLM，60 分鐘音檔請求體約 230 MB。既有架構問題，而依實際負載（1 至 2 分鐘）無虞。接近 spec 上限時需改檔案路徑或分塊傳輸，**不要退回低取樣率**。

### 8.5 aligner 容器以 root 執行

與 vllm 容器一致，且 `HF_HOME` 下的權重層若 chown 會整份複製一次。代價寫在 Dockerfile 註解裡：本服務以 libsndfile 解碼外部來源的音訊，而 libsndfile 有緩衝區溢位的 CVE 史。目前的緩解是該容器不對外且輸入已先經 ffmpeg 正規化。要收斂就建非 root 使用者並以其身分執行 `snapshot_download`，需重新 build 驗證。

**注意這個緩解的前提**：它建立在「aligner 不對外」之上。若日後為了 debug 在 compose 裡把 9100 映射出去，整段緩解即失效，屆時應先處理非 root，或至少限制映射的來源位址。

注意 bff 容器已是非 root（uid 1001），與 aligner 不同，這是 #33 的 volume owner 陷阱的來源。
