# Vibe-Vox 交接文件

**日期**：2026-08-07
**分支**：main @ `0d65a78`（工作樹乾淨，已 push）
**範圍**：架構審查的四個候選落地（`27edf5b` `c14bc90` `de579bd` `0d65a78`）。前一輪的 #6 TTS 合成路徑見第 2 節。

---

## 0. 一句話現況

**#6 把合成路徑接到了真實的 vLLM-Omni，但仍然沒有一次真的合成過。** 整條路徑每一段都有測試，卻沒有一段跑過真的 GPU——tts 服務從未啟動、`docker/tts.Dockerfile` 從未 build 過、三個模型能否共存也還沒量（#31）。這件事**四個架構深化沒有改變**，它仍然是最前面的阻塞——先把它跑起來，不要再往上疊功能。

本輪做的是內部結構：對齊 seam 的回傳型別、模型服務例外的歸位、「一次辨識」有了 module、TTS 文字安全的兩層缺陷。**消費端契約的回應形狀完全未變**（`/api/asr/transcribe` 與 `/api/tts/speech` 的 key 集合逐一比對過），但有一項消費端可見、一項部署可見的**行為**變更，見 §3.2。

---

## 1. 接手第一件事：把 tts 服務起起來

```
docker compose --profile tts up tts
```

然後四件事，順序有意義：

1. **確認 image build 得起來。** `docker/tts.Dockerfile` 從未跑過。`vllm/vllm-omni:v0.24.0` 是 Docker Hub 上實際存在的最新版本 tag（官方安裝文件寫 v0.26.0，那個 tag 尚未發布，文件領先了 registry）。**官方 image 是否已含 `voxcpm` 套件未查證**，故 Dockerfile 自行 `pip install voxcpm soundfile ninja`——若 image 本來就有，那幾行冗餘但無害；若沒有而我漏了別的相依，服務會起不來。
2. **量 VoxCPM2 的實際 VRAM。** 帶 `gpu_uuid`，且**等它完全啟動後**才有意義（啟動途中的讀數會低估數 GB，前一輪踩過）。compose 的 `--gpu-memory-utilization 0.17` 與 `--kv-cache-memory-bytes 1 GiB` 是**未實測的保守起點**，一定要照實測改 → #31。
3. **打一次 `POST /api/tts/speech`。** 確認端點真的回 48 kHz、降採樣後聽起來正常、Instruction 在真模型上真的有作用。
4. **量首音延遲與 RTF。** #17 才問得到我們自己的系統。

### 提交流程

專案慣例是提交前跑 clean-code 與 code-review，有 pre-commit gate 擋著（`~/.claude/hooks/require-review-before-commit.ps1`）。

**唯一可行的走法：這一輪 invoke skill、下一輪才 commit。** hook 讀 transcript 的時機早於該輪寫入，同一輪內先 invoke 再 commit 一定被擋。本輪四次提交都照這個節奏走，四次都一次過。

判準是「上一次**成功**的 commit 之後有沒有跑」，被 gate 擋下的嘗試不計入。

---

## 2. 動之前先讀完的三條線

### 2.1 已實作 ≠ 已驗證

所有 TTS 行為的依據仍是逐行讀 vLLM-Omni 原始碼取得的，沒有一次經過真的模型。`docs/api/tts.md` **逐項標示實作狀態**，三個缺口寫在文件開頭：分塊串流與 mp3 帶了回 400、TN + G2P 前處理層是靜默的品質落差。

### 2.2 Instruction 要寫聲學描述，寫情緒名稱沒有效果

完整數據見 `docs/superpowers/specs/2026-08-05-voxcpm2-style-control-measured.md`。結論：雜訊底線是長度 ×1.18／音量 ×1.32；`(生氣、大聲吼、語速很快)` 量到 ×1.88／×1.55（遠超雜訊），而 `(開心)`／`(難過)`／`(不耐煩)` 全在雜訊內。中文前綴強於英文。

已據此改掉契約、`CONTEXT.md` 的 Instruction 詞條、`docs/spec.md` 與前端說明文字。**唯一沒隔離的變項見第 6 節。**

### 2.3 定版是功能上的必要條件，不是效能優化

zero-shot 同一段描述三次離散 69%，使用者聽感是**三個不同的人**；帶參考音三次離散 18.2%，聽感是同一個人。所以 design 音色若在合成時才從描述重生，同一段對話會逐句換人。ADR-0002 的「建立時定版」沒有它，多句對話不可用。

---

## 3. 本輪完成的四個架構候選

來源是 `/improve-codebase-architecture` 產的八候選報告（在 scratchpad，已隨 session 消失；剩下四個候選的完整內容抄在第 4 節）。

| | 候選 | commit |
|---|---|---|
| C2 | 對齊結果攜帶切片範圍與缺漏原因 | `27edf5b` |
| C4 | 錯誤模式搬進 `AsrClient`／`TtsClient` interface | `c14bc90` |
| C1 | 給「一次辨識」一個 module | `de579bd` |
| C3 | 中性化收斂到不動點、判空量在中性化之後 | `0d65a78` |

四個補償點退場（`alignment._bounds`、`aligner._log_dropped_batches`、`api/asr.py::_align_or_degrade`、`aligner_failed` 布林），一個死欄位（`TranscriptionResult.duration`）與一個死設定（`Utterance` 的 `validate_assignment`）清掉。測試：後端 222 passed / 5 skipped、前端 59 passed（皆為本輪實跑）。

決策細節在 ADR-0004 的 Consequences（本輪新增三段）與各 commit message，此處不重複。

### 3.1 順帶修掉的四個真 bug

1. **落界判準的格點錯位。** `alignment._bounds` 用未量化的段界重算，而 Word 時間戳取三位小數，恰好對到切片邊界的字會被判為落在該段音訊之外。ADR-0004 記載該判準「正常路徑下不會觸發」，所以它一旦誤觸發會被讀成上游行為改變。修法是 `Slice.bounds` 兩端各向外取到毫秒格點。
2. **分批後的逾時預算失效。** #36 改分批送出後，對齊最壞耗時變成「批數 × 逾時」而 `heavy_request_budget()` 只加一次。63 段的錄音（8 批）會讓 guard 先觸發回 504，**逐字稿一併喪失**——正是 ADR-0004 第二層降級要避免的。改為所有批次共用一份預算，以 `asyncio.timeout` 施加。
3. **判空量在中性化之前。** `<|im_end|>` 的字母通得過判空，中性化後整段被移除，於是空的 `Utterance` 佔掉 heavy guard 額度、打一次 GPU、回空音訊 200，而契約要 400。
4. **中性化本身不收斂**（見 §5.1）。

### 3.2 部署與消費端要知道的三項變更

- **只含控制語法的 `input` 從 200 變 400 `EMPTY_INPUT`**（消費端可見）。例如 `<|im_end|>`：舊行為回一段空音訊 200，新行為依契約回 400。這是修正契約違反而非契約變更（`docs/api/tts.md` §6 本來就寫「或經正規化後為空」），但依賴舊行為的呼叫端會看到差異。
- **對齊的逾時語義從「每批」變成「整體預算」**（部署可見）。`VIBE_VOX_ALIGNER_TIMEOUT_SECONDS` 的意義變了：它現在是所有批次共用的一份，不是每批各有一份。
- `AlignerClient.align` **不再拋出對齊層的例外**（內部）。服務掛掉、逾時、某批失敗都轉成該段的 `omission`，ADR-0004 的第二層降級因此成為 interface 的保證而非呼叫端的紀律。

---

## 4. 剩下的四個架構候選

報告已消失，以下是可執行的完整內容。四個都通過 deletion test，不推翻任何 ADR 的 Decision。

### 4.1 C6 — 管理平面的清單編輯狀態機無歸屬（純前端，建議先做）

**問題**：`HotwordsPanel.tsx:42-50` 與 `VoicesPanel.tsx:52-60` 有字面相同的 `run()`（執行變更 → 清錯 → 重抓 → 捕捉錯誤），差別只有 `load` 的參數個數。而 `loading` 狀態只有送模型端點的兩個 Panel 有，清單類的兩個沒有，於是**「載入中」與「真的空」在 UI 上無法區分**：

- `VoicesPanel.tsx:157-158` 首次 render 就顯示「尚未建立任何音色」
- `TtsPanel.tsx:46,61,112` 清單未回時擋住送出並說「請先建立一個」
- `VoicesPanel.tsx:100,158` error 與「尚未建立」同時顯示

這直接違反 CONTEXT.md 的**能力感知**（介面只提供模型實際具備的能力，不讓使用者對不存在的能力下指令）——它對操作者宣告了一個假的系統狀態。

**解法**：`{loading | ready | empty | error}` 四態成為一個 module 的 interface，三個 Panel 消費它。`App.tsx:17-20` 的 `HealthState` 已經是這個形狀，前端目前有兩套判別式狀態。

**deletion test**：刪掉兩份 `run()` 中的任一份不可能——沒有共同歸屬地正是重複的成因。就地展開則 6 個變更操作各自長出 try/catch（估算約 40 行，未實作驗證）。

**順帶可收**：9 處 `err instanceof Error ? … : "字面 fallback"`（各 module 已保證只拋 `Error`，else 分支全部不可達）、兩份 `unwrap`、四份 `errorMessage`（`asr.ts` 那份有 504 邏輯要留）、20 行死碼 `SectionPanel`（`App.tsx:88-107`，四個 `SectionKey` 都被顯式接上，fallback 不可達）。

### 4.2 C7 — 參考音的可用性該是 Voice 的不變量（對應 #44、#45）

**問題**：一條不變量有兩個文件化 owner、零個實作 owner。`docs/spec.md:95` 說 adapter 該驗時長，`adapters/vllm_omni_tts.py` 的 `_data_url` 註解反過來說「正確的防線在建立音色時」，而 `api/admin_voices.py` 的建立路徑沒驗。超界的 Voice 每次合成都回 502，而契約把該碼標為可重試 → 消費端退避重試一個永久失敗。

同一個 `_data_url` 還有第二個問題：`path.read_bytes()` 是**同步 I/O 跑在 async event loop 裡**，而 `config.audio_max_bytes` 是 200 MiB 且被 `admin_voices.py` 的 `create_clone_voice` 直接當參考音上限。一個 200 MiB 的參考音會讓**每一次合成**在 event loop 內同步讀 200 MiB 並編出 267 MiB 字串，期間整個 BFF（含 `/api/health` 與所有 ASR 請求）停住。

**解法**：Voice clone 的上傳走 `AudioIntake` 這個 seam，讓參考音的可用性（存在、可讀、容器合法、時長合規）在建立時成為 Voice 的不變量；參考音的大小上限與 ASR 上傳解耦。

**注意**：`admin_voices.py` 呼叫的 `save_upload` **已經會嗅容器並拋 `UnsupportedAudioFormat`**（`intake.py:45-46`）——前一版交接文件寫「任意檔案都存得進音色目錄」是**錯的**。缺的是時長（#44）與檔案可讀性（#45），不是容器型別。

### 4.3 C5 — Voice 沒有型別，SQL DDL 成了對外契約

**問題**：`persistence/voices.py:72-80` 的 `list()`／`get()` 回 `dict(row)`（`SELECT *` 直轉），於是 `voices` 表的 DDL 就是管理平面的回應形狀與前端型別：`api/admin_voices.py:96-98` 無投影地把整列丟出去（含伺服器檔案系統路徑），`frontend/src/voices.ts:6-16` 逐欄鏡射該 DDL。**表加一欄，對外形狀就變了，而中間沒有任何一行程式碼需要修改，也沒有任何測試會失敗。**

三個 module 用字串常數知道欄名（`voice["ref_audio_path"]`），改欄名不會有型別錯誤，只會在執行期 `KeyError` → 500。

**deletion test**：`VoiceRepository` 該留（刪掉會把 SQL 與唯一性語意複製到 3 處）。可刪的是 `files/cleanup.py:48-55` 那 8 行裸 SQL——它繞過 `persistence/db.py` 的 `connect()`，因此沒有 WAL 也沒有 `busy_timeout=5000`，而 `test_db.py` 正是為那兩個 PRAGMA 存在的。

**順帶可收**：`voices.instruct` 欄位無 writer 無 reader（違反該 module 自己「不存沒有已知用途的欄位」的理由）；`HotwordRepository` 回 `None`／`False` 而 `VoiceRepository` 自己拋，同一目錄下兩個姊妹 module 對「列不存在」的錯誤模式相反。代價是 `admin_hotwords.py` 帶 3 份手工存在性檢查，而那 3 份**還分成兩種形狀**（2 份判 `None`、1 份判 `False`）——不一致不只跨 module，同一個 module 內就有。

### 4.4 C8 — 消費端契約有 10 對手工同步的形狀

**問題**：除 `/api/tts/speech`（`-> Response`，回二進位音訊）與 `GET /api/hotwords`（`-> list[dict]`）外，端點的回傳型別註記都是裸 `dict`，FastAPI 因此產不出回應 schema，`/openapi.json` 無法作為前端型別的來源。前後端的形狀全靠手工同步、無任何契約測試比對。

**最高風險是 `AsrSegment.aligned`**：ADR-0003 定它為契約唯一「同一欄位有兩種語義」之處（`Start`／`End` 的意義隨它翻轉）。後端若改名，TypeScript 仍對著手寫型別編譯成功，執行期 `undefined` → falsy → **全段渲染「未對齊」，把模型自選的切點時間戳當成已檢查過的結果呈現**。失效靜默且語義錯誤，而測試的 mock 自帶該欄位也不會失敗。

**解法**：端點宣告回應型別；跨部署單元的邊界用 repo **已有**的技術守住——`bff/tests/test_config.py:28-46,60-87,100-143` 對 nginx.conf、aligner config、docker-compose.yml 都是「實際去讀對方的檔案」比對，理由寫在 `:31-32`（「字面值的斷言防不了那種失效：把設定刪掉，斷言照樣過」）。那項 leverage 目前沒有指向前端。

**順帶可收**：消費端 `DELETE /api/hotwords/{id}` 對不存在的 id 回 200，管理平面同一個呼叫回 404——超集關係已破且無測試守著；同一個 delete 操作在三個 router 有三種回應形狀（`admin_voices.py:110` 用了消費端形狀）。

---

## 5. 給接手者的警告

### 5.1 只調順序修不掉不收斂的函式（本輪）

C3 原本的診斷是「判空與中性化的順序錯了」，修法是把兩者合成一個步驟。**那個修法對真正的缺陷無效。**

`<\|.*?\|>` 是非貪婪匹配，移除一層後殘骸可能重新組成合法標記：`<<||>|x|>` 的中間段 `<||>` 被吃掉後剩 `<|x|>`。中性化在合成路徑上跑兩次，不收斂就代表兩次結果不同——判空看到的字串與真正送出去的不是同一個。實測：只調順序的版本，`input="<<||>|你好|>"` 仍回 200 並送出空 `Utterance`；instruct 路徑更糟，`<|x|>` 原樣進模型。

**先確認每個步驟自己是冪等或收斂的，再談它們的順序。** 同一缺陷在 ASR 側的 `hotword_text.sanitize_text` 也有，已一併修。

### 5.2 候選的前提會被前一個候選改掉（本輪）

架構報告寫 C4「三個 seam（TTS／ASR／aligner）一次修完」。但 C2 已經讓 aligner 的兩個例外就地轉成 `omission`、不再跨 seam——照抄報告會把 implementation 細節提到 interface 上，方向相反。

**依序執行候選時，每個候選開始前重新驗證它的前提。**

### 5.3 讀來的不是知道的

前幾輪最大的問題是把官方文件當成驗證。三種犯法會重演：

- **讀文件當驗證。** README 列了三種模式就宣稱「走 design 不需要錄音」，而 Voice design 從來沒被任何 spike 測過。
- **讀原始碼但讀錯版本。** `VoxCPM._generate` 在 GitHub `main` 有 `seed` 參數，發行版 `voxcpm 2.0.3` 沒有。**`main` 的 HEAD 不等於會裝到的東西。**
- **測錯層。** 用 `voxcpm` PyPI 套件測，但 production 走 vLLM-Omni 自己的實作。

### 5.4 沒有雜訊基線的量測不可解讀

連跑兩輪 spike 才想到建雜訊底線。在那之前 `(生氣)` 的音量 ×1.36 一度被當成訊號——它其實貼在 ×1.32 的雜訊邊緣。**同一輸入跑三次先量離散度，再解讀任何差異。**

同一輪還有一個量測直接失效：`detect_pitch_frequency` 在 48 kHz 上以預設參數量到標準差 900 Hz，而人聲基頻標準差不可能大於平均。**指標本身要先驗證量得到你要的東西。**

### 5.5 註解不能宣稱不存在的機制

四次了，每次形狀不同：

1. `repository.delete` 的 docstring 寫「實體檔留給清理程序回收」，而那個回收者不存在。
2. #6 的前端加了 `eslint-disable-next-line`，而這個 repo 沒有 eslint 設定。
3. `Utterance` 的 docstring 宣稱的保證範圍大於實作（`model_construct` 與 `model_copy(update=)` 繞得過）。本輪已修：docstring 收斂到實際範圍，並用測試釘住那兩條逃生口確實存在。
4. **本輪的前一版交接文件**寫「`admin_voices.py` 完全沒呼叫 `detect_audio_format`，任意檔案都存得進音色目錄」——`save_upload` 內就會嗅。

**寫「由 X 處理」之前，先確認 X 存在、而且涵蓋你宣稱的範圍。反過來，寫「X 沒做」之前，先確認它沒有透過別的呼叫做掉。**

### 5.6 審查跑到一半斷掉不等於審過

#6 的第二輪 code-review 回報「No findings survived verification」，實際 stats 是 `candidates: 61, verified: 0`——61 個候選缺陷產出後，47 個 verifier 全部因週限額失敗。那不是「審過沒問題」，是「審到一半斷電」。

**看 stats 不要只看結論。** 本輪四次審查都完整跑完，兩軸各自找出真缺陷——其中兩個是我沒看到的（§5.1 的不收斂、格點錯位只修一半）。**審查的價值在它會推翻你的診斷，不在它蓋章。**

### 5.7 available skills 清單不是安裝清單

那份清單只是 agent 能主動 invoke 的子集，使用者以 `/plugin:skill` 叫得起清單外的。**要判斷 skill 在不在，看 `~/.claude/plugins/cache/<owner>/<plugin>/<version>/skills/`。** 在建議流程時不要因為「我叫不起來」就排除某條路徑——那是 agent 的限制，不是環境的限制。

### 5.8 其餘仍然有效的教訓

不要把答案寫進問題裡（測情緒時提示詞含「大聲吼、語速很快」，量到的是自己要求的東西）、使用者的耳朵與數字是兩把不同的尺、問句不是工單（要做功能就開票）、沒做出來的東西不要為它寫驗證、驗證要從既有狀態出發、推算的數字要明說是推算、沒設定的預設值也是配置（nginx 的 60 秒）、測試資料的規模要貼近真實負載。

完整脈絡見 git history 中 `824f723` 版本的本檔第 5 節。

---

## 6. 資源位置

**權威文件**（單一真實來源，交接文件不重複其內容）：

- `docs/api/asr.md` — ASR 消費端契約，全文皆為已實作行為
- `docs/api/tts.md` — TTS 消費端契約，逐項標示實作狀態
- `docs/adr/0004-word-level-forced-alignment.md` — 字級對齊的決策、GPU 實測數據，與本輪新增的三段 Consequences（鏈路歸屬、逾時預算、對齊結果的形狀）
- `CONTEXT.md` — 領域詞彙。本輪新增「一次辨識（Transcription）」與「對齊缺漏（Omission）」兩個詞條
- `docs/superpowers/specs/2026-08-05-voxcpm2-serving-transport.md` — 傳輸選型，逐行讀原始碼取證
- `docs/superpowers/specs/2026-08-05-tts-text-frontend-tn-g2p.md` — ttsfrd 判定與 TN／G2P 選型
- `docs/superpowers/specs/2026-08-05-voxcpm2-style-control-measured.md` — 風格控制實測，含雜訊底線與量測失敗紀錄

**ADR-0001 標 `superseded`**，取代它的新 ADR 是 #19。**ADR-0003 的串流條目已加註實作狀態**，決策未撤回。

**spike harness**：`spike/voxcpm-tts` 分支（真人參考音、GPU）。

**遠端 GPU 機**：`http://10.2.66.102:8088`。只開 HTTP 8088，無 shell 存取。

**開放中的票**：#6 #7 #8（TTS 功能）、#13（wayfinder map）、#17–#20（TTS 決策票）、#31 #32 #39 #40 #41（校準與部署）、#43（麥克風錄音）、#44 #45（參考音驗證，見 §4.2）。

---

## 7. 唯一沒隔離的變項

`2026-08-05-voxcpm2-style-control-measured.md` §5：**所有風格控制的實測都用合成參考音**（zero-shot 產物，平板）。

情緒在 voice cloning 中從參考音繼承表現力，故「情緒標籤無效」有兩種解釋，無法區分：模型的通道本身不處理情緒標籤，或通道有效但合成參考音沒有情感範圍可供調度。**唯一的正面證據不是這幾輪測的**：spike #12 以真人台灣參考音在情緒項 PASS。

關閉方式：一段有表情的真人台灣錄音（5 至 30 秒）當參考音，重跑那五組。#43 一做出來就能取得這個素材——而 tts 服務起來之後，這件事就只差那段錄音。
