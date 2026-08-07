# Vibe-Vox 交接文件

**日期**：2026-08-07
**分支**：main @ `268ef9a`（已 push）
**範圍**：C6 落地（`addb649`）、TTS 記憶體記載同步（`78c8ffc`）、served-model-name 修正（`268ef9a`），以及 **TTS 合成路徑首次真機驗證**。

---

## 0. 一句話現況

**TTS 從頭到尾跑通了。** 服務起得來、三個模型共存於 GPU 0、`POST /api/tts/speech` 產出真的音訊、契約凍結的 24 kHz／單聲道經實測、Instruction 在真模型上有效。前一版交接文件說「最前面的阻塞是沒有一次真的合成過」——那個阻塞解除了。

剩下的是功能與品質，不再是「這東西到底能不能動」。

---

## 1. 接手第一件事

**沒有單一阻塞了，三條路都可以走。** 依價值排序：

1. **C7（§4.1）** — 含一個真的 production 缺陷：200 MiB 的參考音會讓 BFF 在 event loop 內同步讀檔並編出 267 MiB 字串，期間 `/api/health` 與所有 ASR 請求全部停住。對應 #44、#45 兩張已開的票。**建議先做這件。**
2. **TN + G2P 前處理層** — `docs/api/tts.md` §5.1 標明的靜默品質落差：數字唸法與台灣破音字現在落到模型的 zh-CN 預設，不報錯只唸錯。管線已選型（`wetext` + OpenCC s2tw + g2pW 台灣注音模型），**尚無票**。這是消費端唯一會抱怨而系統不會提示的問題。
3. **C5、C8（§4.2、§4.3）** — 型別與契約的整理，價值較低但風險也低。

`/implement C7` 可以直接開始——§4 的內容就是規格，不需要先走 `/grill-with-docs` 或 `/to-spec`。

### 提交流程

提交前跑 clean-code 與 code-review，有 pre-commit gate 擋著。**唯一可行的走法：這一輪 invoke skill、下一輪才 commit。** hook 讀 transcript 的時機早於該輪寫入，同一輪內先 invoke 再 commit 一定被擋。判準是「上一次**成功**的 commit 之後有沒有跑」。

---

## 2. TTS 的實測結論

本節給結論與能據以決策的關鍵數字。**完整量測（方法、原始數據、失敗的那幾輪）在 issue**——交接文件會被改寫，issue 不會。

### 2.1 Instruction 有效，但只能控語速（#18）

`生氣、大聲吼、語速很快` 對長度的效果是 −10.2%，Mann-Whitney U=50（n=20 對 20，臨界值 127），分布明顯分離。**響度只有 +1.45 dB 而且小於它自己的變異（標準差 2.42 dB）——不要用 instruct 控音量**，要穩定響度得在消費端正規化。

**spike 的 ×1.88／×1.55 不適用於本服務。** 那是 `voxcpm` PyPI 套件加合成參考音量的；同一句 instruct 在 vLLM-Omni 加真人參考音上只有 ×1.11。契約已改為引用實測值。

前一版交接文件 §7 記的「唯一沒隔離的變項」（所有風格實測都用合成參考音）**已關閉**：這次用真人錄音當參考音，效果依然集中在語速。

### 2.2 這條路徑自己的雜訊底線（#18）

無 instruct、同一句話跑 20 次：長度標準差 0.30 秒（7.6%）、範圍 3.52–4.80 秒；響度標準差 2.42 dB。**之後任何風格量測以此為基準**，不要再用 spike 的 ×1.18。

### 2.3 延遲與 VRAM（#17、#31）

單句 19 字合成往返 **0.66 秒**（n=6、離散 6.5%、非串流）。`VIBE_VOX_TTS_TIMEOUT_SECONDS=120` 比穩態高兩個數量級，**先不動**——它覆蓋的冷啟與長文字都還沒量。

TTS 推論期只比閒置常駐多 122 MiB。但 **ASR 跑過一次長音檔會讓 vLLM 佔用長高約 9 GB 且不歸還**，所以「GPU 0 還剩 11 GB」是假象。三者同時推論仍未測，那是 #31 剩下最實質的一項。

### 2.4 定版是功能上的必要條件

zero-shot 同一段描述三次離散 69%，聽感是**三個不同的人**；帶參考音三次離散 18.2%，聽感是同一個人。ADR-0002 的「建立時定版」沒有它，多句對話會逐句換人。

---

## 3. 本輪完成

| commit | 內容 |
|---|---|
| `addb649` | C6：清單狀態成為一個 module（`frontend/src/collection.ts`），四態判別式取代「陣列 + error 字串」 |
| `78c8ffc` | ASR utilization 預設 0.70 → 0.65，TTS 記憶體參數改記實測值，六處記載同步 |
| `268ef9a` | tts 服務漏設 `--served-model-name`，每次合成都被 vLLM 以 4xx 拒絕 |

測試：後端 225 passed / 5 skipped，前端 72 passed（皆本輪實跑）。

### 3.1 部署可見的變更

- **`VIBE_VOX_VLLM_GPU_MEMORY_UTILIZATION` 預設 0.65。** 0.70 之下 VoxCPM2 在啟動期因 free memory 不足被拒。0.65 是被 TTS 逼出來的、不是 ASR 自己的需求，且**沒有做 0.70 那樣的逐字元比對**。只跑 ASR 時可以調回 0.70。
- **`VIBE_VOX_TTS_SERVED_NAME`**（新，預設 `voxcpm2`）。同一個表示式寫在 compose 的 bff 與 tts 兩處，單邊改的症狀是每次合成在 0.03 秒內回 502。

### 3.2 消費端可見的變更

- 管理平面三個 Panel 現在區分「載入中」與「真的空」。先前首次 render 就顯示「尚未建立任何音色」、載入失敗時錯誤與空狀態同時出現，那是對操作者宣告假的系統狀態（違反 CONTEXT.md 的能力感知）。
- `GET /api/tts/models` 的回傳值改由 `settings.tts_served_name` 導出，不再寫死。

---

## 4. 剩下的三個架構候選

來源是 `/improve-codebase-architecture` 產的八候選報告（已隨 session 消失）。四個已落地（C1–C4）、C6 本輪完成，以下三個是可執行的完整內容，通過 deletion test，不推翻任何 ADR 的 Decision。

### 4.1 C7 — 參考音的可用性該是 Voice 的不變量（#44、#45）

**問題**：一條不變量有兩個文件化 owner、零個實作 owner。`docs/spec.md` 說 adapter 該驗時長，`adapters/vllm_omni_tts.py` 的 `_data_url` 註解反過來說「正確的防線在建立音色時」，而 `api/admin_voices.py` 的建立路徑沒驗。超界的 Voice 每次合成都回 502，而契約把該碼標為可重試 → 消費端退避重試一個永久失敗。

同一個 `_data_url` 還有第二個問題：`path.read_bytes()` 是**同步 I/O 跑在 async event loop 裡**，而 `config.audio_max_bytes` 是 200 MiB 且被 `create_clone_voice` 直接當參考音上限。一個 200 MiB 的參考音會讓**每一次合成**在 event loop 內同步讀 200 MiB 並編出 267 MiB 字串，期間整個 BFF（含 `/api/health` 與所有 ASR 請求）停住。

**解法**：Voice clone 的上傳走 `AudioIntake` 這個 seam，讓參考音的可用性（存在、可讀、容器合法、時長合規）在建立時成為 Voice 的不變量；參考音的大小上限與 ASR 上傳解耦。

**注意**：`admin_voices.py` 呼叫的 `save_upload` **已經會嗅容器並拋 `UnsupportedAudioFormat`**（`intake.py:45-46`）。缺的是時長（#44）與檔案可讀性（#45），不是容器型別。

### 4.2 C5 — Voice 沒有型別，SQL DDL 成了對外契約

**問題**：`persistence/voices.py` 的 `list()`／`get()` 回 `dict(row)`（`SELECT *` 直轉），於是 `voices` 表的 DDL 就是管理平面的回應形狀與前端型別：`api/admin_voices.py` 無投影地把整列丟出去（含伺服器檔案系統路徑），`frontend/src/voices.ts` 逐欄鏡射該 DDL。**表加一欄，對外形狀就變了，而中間沒有任何一行程式碼需要修改，也沒有任何測試會失敗。**

三個 module 各自用字串常數知道欄名，改欄名不會有任何型別錯誤，而三者的失敗方式**互不相同**：`api/tts.py` 的 dict 下標得到 `KeyError` → 500；`api/admin_voices.py` 的 kwarg 得到 `TypeError`；而 `files/cleanup.py` 的裸 SQL 得到 `sqlite3.OperationalError`，**被該函式的 `except sqlite3.Error: return []` 靜默吞掉——孤兒檔從此永不回收，沒有任何錯誤浮出水面**。最後那個比 500 更糟。

**deletion test**：`VoiceRepository` 該留（刪掉會把 SQL 與唯一性語意複製到 3 處）。可刪的是 `files/cleanup.py` 那 8 行裸 SQL——它繞過 `persistence/db.py` 的 `connect()`，因此沒有 WAL 也沒有 `busy_timeout=5000`，而 `test_db.py` 正是為那兩個 PRAGMA 存在的。

**順帶可收**：`voices.instruct` 欄位無 writer 無 reader；`HotwordRepository` 回 `None`／`False` 而 `VoiceRepository` 自己拋，同一目錄下兩個姊妹 module 對「列不存在」的錯誤模式相反，代價是 `admin_hotwords.py` 帶 3 份手工存在性檢查而那 3 份還分成兩種形狀。

### 4.3 C8 — 消費端契約有 10 對手工同步的形狀

**問題**：20 個 route handler 中只有三個不是裸 `dict`（`/api/tts/speech` 與 `/api/admin/hotwords/export` 回 `-> Response`、`GET /api/hotwords` 回 `-> list[dict]`），其餘的回傳型別註記都是裸 `dict`，FastAPI 因此產不出回應 schema，`/openapi.json` 無法作為前端型別的來源。前後端的形狀全靠手工同步、無任何契約測試比對。

**最高風險是 `AsrSegment.aligned`**：ADR-0003 定它為契約唯一「同一欄位有兩種語義」之處（`Start`／`End` 的意義隨它翻轉）。後端若改名，TypeScript 仍對著手寫型別編譯成功，執行期 `undefined` → falsy → **全段渲染「未對齊」，把模型自選的切點時間戳當成已檢查過的結果呈現**。失效靜默且語義錯誤，而測試的 mock 自帶該欄位也不會失敗。

**解法**：端點宣告回應型別；跨部署單元的邊界用 repo **已有**的技術守住——`bff/tests/test_config.py` 對 nginx.conf、aligner config、docker-compose.yml 都是「實際去讀對方的檔案」比對。那項 leverage 目前沒有指向前端。

**順帶可收**：消費端 `DELETE /api/hotwords/{id}` 對不存在的 id 回 200，管理平面同一個呼叫回 404——超集關係已破且無測試守著。

### 4.4 C6 的順帶項未收

`hotwords.ts` 與 `voices.ts` 的兩份 `unwrap`、四份 `errorMessage`、`App.tsx:89-108` 的 `SectionPanel` 死碼。前兩者要動 API client 層，不屬清單狀態機的主題。

---

## 5. 給接手者的警告

### 5.1 部署的 image 落後於 repo，沒有任何機制會發現

本輪最初的 404：`POST /api/admin/voices/clone` 回 `{"detail":"Not Found"}`，而本機同一路徑回 201。原因是遠端 bff 容器的 image build 在音色 CRUD 實作之前。

**沒有任何東西會提示這件事。** 容器「Up 30 minutes」講的是啟動時間，不是 image 的建置時間；`/api/health` 照回 200，因為它探測的是模型服務不是自己的版本。

診斷起手式：`curl -sS http://localhost:8088/api/health` 之外，加一行
`docker exec vibe-vox-bff-1 python -c 'import urllib.request,json;print("\n".join(sorted(json.load(urllib.request.urlopen("http://127.0.0.1:8000/openapi.json"))["paths"])))'`
——它列出 BFF 實際認得的路徑。**`/openapi.json` 不在 `/api/` 底下，經 nginx 8088 拿到的是 `index.html` 不是 JSON**，必須從容器內部打。

### 5.2 改設定要重建容器，profile 服務要帶 profile

`docker compose up -d tts` 不帶 `--profile tts` 時 compose 不認得 tts 服務；改了 command 或 environment 不帶 `--force-recreate` 則容器照舊跑。兩者都不會報錯，只會讓你以為改了。

### 5.3 比較兩筆量測之前，先確認條件相同

本輪拿 14:15 與 16:09 的 VRAM 讀數比較，推論出「utilization 設定沒生效」，並據此質疑操作者的正確陳述。實情是那兩筆來自**不同的容器**（`docker ps` 的 `CreatedAt` 差 51 分鐘），且一筆跑過 ASR 長音檔、一筆沒有——差的 9 GB 是 PyTorch 配置後不歸還的推論快取。

**量測要記「跑過什麼」而不只是「幾點量的」。** 比較之前先跑 `docker ps --format '{{.Names}}\t{{.CreatedAt}}'`。

### 5.4 指標本身要先驗證量得到你要的東西

風格控制量測的頭兩輪都失敗，兩次都是指標的問題不是結論的問題：

- **`duration` 被量化成 0.16 秒的步階**。4 秒的音訊上解析度下限就是 4%，小於此的差異在這個指標上根本不存在。
- **`volumedetect` 的 `mean_volume` 含頭尾靜音**，靜音長度的變化會被讀成音量變化。改用 `ffmpeg -af ebur128` 的 integrated loudness 後，第一輪的離群值不復現。

同類舊例：`detect_pitch_frequency` 在 48 kHz 上以預設參數量到標準差 900 Hz，而人聲基頻標準差不可能大於平均。

### 5.5 沒有雜訊基線的量測不可解讀

n=5 的兩組完全交錯（U=8 對臨界值 2），n=20 才分得開。每次合成只要 0.66 秒，40 次不到一分鐘——**沒有理由用 n=5**。那個規模是 spike 時期 GPU 昂貴的取捨，現在不成立。

### 5.6 註解裡提到某個設定，不等於那個設定存在

第五次了。本輪：compose 的註解寫「ASR 側在 `vllm.Dockerfile` 設了 `--served-model-name vibevoice`」——該檔只在**註解裡提到**上游腳本會帶，Dockerfile 自己沒設。code-review 抓到的。

前四次：`repository.delete` 的 docstring 寫「實體檔留給清理程序回收」而那個回收者不存在；#6 的前端加了 `eslint-disable-next-line` 而這個 repo 沒有 eslint 設定；`Utterance` 的 docstring 宣稱的保證範圍大於實作；前版交接文件寫「`admin_voices.py` 完全沒驗格式」而 `save_upload` 內就會嗅。

**寫「由 X 處理」之前，先確認 X 存在、而且涵蓋你宣稱的範圍。**

### 5.7 `curl -s` 會把連線層的錯誤一起吞掉

本輪浪費了一輪診斷：第一次請求「完全沒反應」，看起來像新症狀，其實是 `-s` 靜默了 curl 自己的錯誤。**一律用 `-sS` 並帶 `-w '\nHTTP %{http_code}\n'`。**

### 5.8 候選的前提會被前一個候選改掉

架構報告寫 C4「三個 seam 一次修完」，但 C2 已經讓 aligner 的兩個例外就地轉成 `omission`、不再跨 seam。**依序執行候選時，每個候選開始前重新驗證它的前提。**

### 5.9 審查跑到一半斷掉不等於審過

#6 的第二輪 code-review 回報「No findings survived verification」，實際 stats 是 `candidates: 61, verified: 0`——61 個候選缺陷產出後，47 個 verifier 全部因週限額失敗。**看 stats 不要只看結論。**

本輪兩次審查都完整跑完，各抓出我沒看到的真缺陷（`api/tts.py` 的 `MODEL_NAME` 寫死、上述 §5.6 的假記載）。**審查的價值在它會推翻你的診斷，不在它蓋章。**

### 5.10 available skills 清單不是安裝清單

那份清單只是 agent 能主動 invoke 的子集。**要判斷 skill 在不在，看 `~/.claude/plugins/cache/<owner>/<plugin>/<version>/skills/`。**

### 5.11 其餘仍然有效的教訓

不要把答案寫進問題裡、使用者的耳朵與數字是兩把不同的尺（本輪兩把都用上：耳朵先說有效，n=20 才證實）、問句不是工單、沒做出來的東西不要為它寫驗證、驗證要從既有狀態出發、推算的數字要明說是推算、沒設定的預設值也是配置。

完整脈絡見 git history 中 `824f723` 版本的本檔第 5 節。

---

## 6. 資源位置

**權威文件**（單一真實來源，本檔不重複其內容）：

- `docs/api/asr.md` — ASR 消費端契約，全文皆為已實作行為
- `docs/api/tts.md` — TTS 消費端契約，逐項標示實作狀態；本輪實測進了 §5.2（風格控制）、§5.3（取樣率）、§5.6（延遲）
- `docs/adr/0004-word-level-forced-alignment.md` — 字級對齊的決策與 GPU 實測數據
- `CONTEXT.md` — 領域詞彙
- `docs/superpowers/specs/2026-08-05-voxcpm2-serving-transport.md` — 傳輸選型，逐行讀原始碼取證
- `docs/superpowers/specs/2026-08-05-voxcpm2-style-control-measured.md` — spike 的風格控制數據（**注意：那條路徑與 production 不同，見 §2.1**）

**本輪量測的歸屬地**：#18（風格控制與降採樣驗證）、#17（延遲）、#31（VRAM）。數字在 issue 不在本檔。

**ADR-0001 標 `superseded`**，取代它的新 ADR 是 #19。

**遠端 GPU 機**：`http://10.2.66.102:8088`。只開 HTTP 8088，無 shell 存取。

**開放中的票**：#6（合成路徑，本輪已跑通，待決定是否關閉）、#7 #8（TTS 功能）、#13（wayfinder map）、#17–#20（TTS 決策票）、#31 #32 #39 #40 #41（校準與部署）、#43（麥克風錄音）、#44 #45（參考音驗證，見 §4.1）。
