# Vibe-Vox 交接文件

**日期**：2026-08-06
**分支**：main
**範圍**：TTS 引擎變更的決策落地（#14 #15 #16）、音色 CRUD 與管理面板、VoxCPM2 風格控制的實測、#6 TTS 合成路徑打通

---

## 0. 一句話現況

**音色管理從佔位變成能用的東西**（`85bfaa7`），而 **#6 把合成路徑從端點一路接到了真實的 vLLM-Omni**：adapter、契約補齊、compose 的 tts 服務、TTS 測試頁與音色試聽都在了。

**但仍然沒有一次真的合成過。** 整條路徑的每一段都有測試，卻沒有一段跑過真的 GPU——tts 服務從未啟動，三個模型能否共存也還沒量（#31）。**下一步是把它起起來，而不是再往上疊功能。**

**本 session 第一次真的跑了 VoxCPM2**（本機 CPU 容器，18 次生成），把六項「讀來的」變成「量到的」。其中一項推翻了先前寫進契約的敘述：**行內 `(...)` 前綴對聲學描述有效、對情緒標籤無效**，數據與使用者聽感一致。

TTS 引擎變更的七張決策票解掉三張（#14 #15 #16），剩 #17–#20。

---

## 1. 接手第一件事：把 tts 服務起起來

程式碼在了，服務沒起過。第一件事是 `docker compose --profile tts up tts`，然後：

- 量 VoxCPM2 的實際 VRAM（帶 `gpu_uuid`，**等它完全啟動後**才有意義），確認三個模型放不放得下 → #31
- compose 的兩個記憶體參數（`0.17` 與 1 GiB KV）是**未實測的保守起點**，一定要照實測改
- 打一次 `POST /api/tts/speech`，確認 48 kHz 真的降到 24 kHz、Instruction 真的有作用
- `docker/tts.Dockerfile` 從未 build 過：`vllm/vllm-omni:v0.24.0` 是 Docker Hub 上實際存在的最新版本 tag，但官方 image 是否已含 `voxcpm` 套件未查證，故 Dockerfile 自行裝了

提交前依專案慣例跑 clean-code 與 code-review（有 pre-commit gate 擋著，且**同一輪內先 invoke 再 commit 會被擋**，要分兩輪）。

---

## 2. 動之前先讀完的三條線

### 2.1 TTS 沒有跑起來過，所有 TTS 行為都是讀來的

#6 交付後，整條路徑的程式碼都在了，**但沒有任何一次合成經過真的模型**。所有 TTS 行為的依據仍是逐行讀 vLLM-Omni 原始碼取得的，不是實測。

`docs/api/tts.md` 現在**逐項標示實作狀態**（原本全文標「尚未實作」）。三個缺口寫在文件開頭：分塊串流與 mp3 帶了回 400、TN + G2P 前處理層是靜默的品質落差。

**已實作 ≠ 已驗證。** 端點會不會真的收到 48 kHz、Instruction 在真模型上是否生效、降採樣後聽起來如何——這三件事都要等服務起來。

### 2.2 Instruction 要寫聲學描述，寫情緒名稱沒有效果

這是本 session 唯一一項**推翻既有契約敘述**的實測。完整數據見
`docs/superpowers/specs/2026-08-05-voxcpm2-style-control-measured.md`。

先建立雜訊底線（同輸入三次）：**長度 ×1.18、音量 ×1.32**。沒有這把尺，前兩輪測試的結論全都不可解讀。

| 前綴 | 長度 | 音量 | 判定 |
|---|---|---|---|
| `(生氣、大聲吼、語速很快)` | ×1.88 | ×1.55 | 遠超雜訊，聽感明顯 |
| `(生氣)` | ×1.05 | ×1.36 | 雜訊邊緣 |
| `(開心)`／`(難過)`／`(不耐煩)` | ×1.00–1.15 | ×1.01–1.17 | **全在雜訊內** |

使用者聽感：極端聲學對比「明顯」，五個情緒詞「差別不大」。

**中文前綴強於英文**（1.55／1.88 對 1.37／1.50），兩個指標一致。

已據此改掉 `docs/api/tts.md` 的 `instruct` 說明與四個範例、`CONTEXT.md` 的 Instruction 詞條、`docs/spec.md` 的 US29 與範例清單要求、前端面板的說明文字。

### 2.3 定版是功能上的必要條件，不是效能優化

zero-shot 同一段描述三次：長度 4.32／2.56／3.36 秒，**離散 69%**，使用者聽感是**三個不同的人**。

帶參考音（reference clone）三次：**離散 18.2%**，聽感是**同一個人、速度些微差異**。

所以 design 音色若在合成時才從描述重生，同一段對話會逐句換人。ADR-0002 的「建立時定版」沒有它，多句對話不可用。

---

## 3. 本 session 完成

| 提交 | 內容 |
|---|---|
| `85bfaa7` | 音色 CRUD 六個端點 + `VoicesPanel` 取代佔位 |
| `931c325` | #16 能力感知矩陣重寫、TTS 敘述全 repo 改 VoxCPM2、新增 `docs/api/tts.md` |
| `08954fe` | #14 #15 的 research findings |

**測試**：BFF 165 passed / 4 skipped（含未提交的 3 條）、frontend 49 passed、typecheck 乾淨、`docker compose config` 通過。

### 3.1 #14 傳輸定案：vLLM-Omni 的 `/v1/audio/speech`

以 GitHub API 逐行讀 `main` 分支原始碼取證，推翻 2026-08-03 那則以文件為據的 comment 共四項判斷。

**最咬人的發現**：`instructions` 與 `task_type` 對 VoxCPM2 **從未被讀取且不報錯**——帶了會回 200 加一段未套用該風格的音訊。風格的唯一通道是把 `(...)` 寫進 `input`。

**官方文件有三處與原始碼矛盾**，其中一處會誤導選型：`speech_api.md` 宣稱 VoxCPM2 有 built-in speaker presets，而 `_load_supported_speakers` 是 `return {"default"}`。

**`gpu_memory_utilization` 是 per-instance 上限**，不是同卡瓜分總額。ADR-0001 的「壓低比例替 TTS 留 VRAM」在機制上就是錯的。

### 3.2 #15 ttsfrd 判定為 blocker，改走開源路徑

使用者指定要加 ttsfrd，查證後**兩個獨立成立的否決理由**，皆經獨立複驗：

**無任何授權可依循**——wheel 的 METADATA 全文 174 bytes 無 License 欄、ModelScope 的三個授權欄位皆空字串、repo 無 LICENSE，且 `resource.zip` 內含明文禁商用的 Festival OALD 語料。

**`.so` 內沒有 ZhTW locale**——中文系只有 `ZhCN`／`ZhHK`／`ZhSC`／`ZhSH`，locale 代碼字串中文只有 `zh-cn`，全檔對 TW／Taiwan／注音零命中。它的中文路徑就是 zh-CN，即使取得授權對台灣仍是錯的工具。

替代方案零額外授權面：**VoxCPM2 內建的正規化器本身就是 `wetext`**（Apache-2.0，已在相依樹）。正面發現：**`g2pW` 發行的 `G2PWModel-v2-onnx` 是台灣注音模型**，輸出格式與 `{le4}` 語法零轉換。

### 3.3 #16 能力感知矩陣重寫

判準由「音色屬於哪一型」改為「該音色以哪一種模式重播」。使用者裁決**平台固定走 Controllable**：合成不送 `ref_text`，兩型音色一律吃 Instruction，故能力感知的「停用 Instruction 欄位」規則整條消失。

**移除了「系統預建 Voice」這個型別。** VoxCPM2 沒有任何內建語者，所謂預建音色就是我方自己建的 clone 或 design——那是「誰建的」而非一種音色。Voice 由三型改為兩型，`type` 去掉 `'preset'`，User Story 44 刪除並重新編號。

### 3.4 音色 CRUD 與管理面板

六個端點：`GET /api/tts/{models,voices}`、`GET /api/admin/voices`、`POST /api/admin/voices/clone`、`PUT`（改名，id 不變）、`DELETE`。前端 `VoicesPanel` 取代「walking skeleton 佔位」。

**跨檔案系統的搬移**：暫存檔搬到音色目錄用 `shutil.move` 而非 `Path.replace`。`/app/var/tmp` 在容器可寫層、`/data/voices` 在 volume，是不同檔案系統，`os.replace` 會 EXDEV。**這在 production 一樣炸**，是測試抓到的。

**刪除音色的孤兒檔**：`delete` 只移除 DB 列（避免與進行中的合成競態），實體檔由啟動時的 `sweep_orphan_voice_files` 回收。判準是「DB 沒有列引用它」而非保留期——用 mtime 會誤刪剛建立的音色。

---

## 4. 下一步

### 4.1 #6 已交付，但三個缺口沒做

交付的：`adapters/vllm_omni_tts.py`（不送 `ref_text`／`instructions`／`task_type`，風格走行內前綴，48 kHz → 24 kHz）、端點契約補齊（`model`／`response_format`／括號中性化／`INPUT_TOO_LONG`／heavy guard／502 與 504 映射）、`docker/tts.Dockerfile` 與 compose 的 tts 服務、`TtsPanel` 與音色試聽。

**沒做且已標註的三項**：

1. **TN + G2P 前處理層**（`tts_text.py` 目前只有控制語法中性化）。#15 已把管線定死到不需再 grill 的程度，但**沒有票**。沒有它，數字唸法與破音字落到 zh-CN 的行為，而且不會報錯。
2. **分塊串流**（回 400 `STREAM_UNSUPPORTED`）。#17 的延遲 bar 未定，且串流實作後才談得上首音延遲。
3. **mp3 輸出**（回 400）。需要編碼器，#6 未列為驗收項。

另有一項已知殘留：FastAPI 對每個帶 body 的端點自動宣告 422，而 main.py 一律轉 400，那個 422 永遠不會發生。`openapi_extra` 是合併不是取代，拿不掉；要修得動所有端點。

### 4.2 剩餘的決策票

`#17` 延遲 bar（需要跑起來的服務才量得到）、`#18` 消費端契約重定、`#19` 新 ADR 取代 ADR-0001、`#20` CONTEXT 的 TTS 詞彙。

`#19` 已登記三項 ADR-0001 的既有衝突。`#20` 待決的只剩 Instruction 是否改名、模式用語要不要入詞彙表。

### 4.3 其他

`#44`（新開，由 #6 的 review 分出）**參考音時長未在建立時驗證**：端點強制 1.0–30.0 秒，我方三層都沒擋，超界的音色每次合成都回 502 `TTS_UNAVAILABLE`——而契約把該碼標為可重試，消費端會退避重試一個永久失敗。順帶發現 `admin_voices.py` 完全沒呼叫 `detect_audio_format`，任意檔案都存得進音色目錄。

`#8` design 建立（定版）、`#43` 麥克風錄音、`#7` 的更換參考音與試聽、`#31` `#32` `#39` `#40` `#41` 未動。

---

## 5. 給接手者的警告

### 5.1 讀來的不是知道的

本 session 最大的問題是我把官方文件當成驗證。使用者原話：「你都不確定，那你為什麼會確定」「你只照本宣科，有什麼功能、有什麼限制、有什麼界線，你都不知道，你就宣稱答案了」。

具體犯法三種，逐一記著因為它們會重演：

**讀文件當驗證。** 官方 README 列了三種模式，我就宣稱「走 design，不需要錄音」。實際上 **Voice design 從來沒被任何 spike 測過**——#11 測台灣讀音、#12 測情緒 vs 保真，兩者都用 clone 路徑。而評估文件自己就寫了這個陷阱：「誠實區分：官方明列語言支援有中文；台灣口語化語感無來源，無法證實。」

**讀原始碼但讀錯版本。** `VoxCPM._generate` 在 GitHub `main` 有 `seed` 參數，**發行版 `voxcpm 2.0.3` 沒有**（實跑得到 `TypeError`）。而我已經把 seed 寫進資料模型了。**`main` 的 HEAD 不等於會裝到的東西。**

**測錯層。** 我用 `voxcpm` PyPI 套件測，但 production 走 vLLM-Omni 自己的實作，根本不呼叫那個套件。參考實作的結論不能直接推到端點。

### 5.2 沒有雜訊基線的量測不可解讀

我連跑兩輪 spike 才想到要建雜訊底線。在那之前，`(生氣)` 的音量 ×1.36 一度被當成訊號——它其實貼在 ×1.32 的雜訊邊緣。

**同一輸入跑三次先量離散度，再解讀任何差異。**

同一輪還有一個量測直接失效：`torchaudio.functional.detect_pitch_frequency` 在 48 kHz 上以預設參數量到 580–725 Hz、標準差 900 Hz，而人聲基頻約 85–255 Hz、標準差不可能大於平均。**指標本身要先驗證量得到你要的東西。**

### 5.3 不要把答案寫進問題裡

我測情緒時用的提示詞是「生氣、**大聲吼、語速很快**」，量到音量與語速改變就差點當成情緒生效。那是我自己要求的東西。

拿掉聲學提示、只給情緒詞重測，才得到真正的答案。

### 5.4 註解不能宣稱不存在的機制

`repository.delete` 的 docstring 原本寫「實體檔留給清理程序回收」——**那個回收者不存在**，`cleanup.py` 只掃 `temp_dir`。刪音色會永久洩漏磁碟，而註解讓它看起來有人管。對抗式審查抓到的。

**寫「由 X 處理」之前，先確認 X 存在。**

### 5.5 使用者的耳朵與數字是兩把不同的尺

RMS 與長度量得到韻律，**量不到情感**。使用者說「不能很肯定是不是生氣，只是聲音大、語速快」——那是數字碰不到的一層。

反過來，使用者聽感也需要數字校準：「中文對比比較大」對應到 1.55／1.88 對 1.37／1.50。

**兩把尺都要，而且要知道各自量不到什麼。**

### 5.6 問句不是工單

使用者問「clone 有做麥克風錄音嗎」，我答完就直接開始寫測試，被質問才回頭補票——順序完全反了。工作區已回復，#43 是票、零程式碼。

而「先做功能」是針對某個具體功能講的，不是跳過流程的通行證。**要做功能就開票。**

### 5.7 沒做出來的東西不要為它寫驗證

我曾為「參考音時長 1.0–30.0 秒」開票（#42，已關）。那個限制是從 vLLM-Omni 原始碼讀來的，而合成端點不存在、TTS 服務沒部署——**從來沒撞到過**。使用者原話：「你功能沒做出來怎麼驗證，一直在空跑，猜測」。

**先讓路徑跑通，有實際的失敗樣態再設計驗證。**

### 5.8 審查跑到一半斷掉不等於審過

#6 的第二輪 code-review 回報「No findings survived verification」，看起來乾淨。實際的 stats 是 `candidates: 61, verified: 0`——61 個候選缺陷產出後，47 個 verifier **全部因週限額失敗**（55 個 agent 只有 7 個完成）。

那不是「審過沒問題」，是「審到一半斷電」。重跑之後兩軸各找出四項實質缺陷，其中兩項會實際咬人（參考音時長 → #44、data URI 謊報容器）。

**看 stats 不要只看結論。** `verified: 0` 配 `candidates: 61` 是失效的訊號，不是好消息。

### 5.9 前面幾輪的教訓仍然有效

驗證要從「既有狀態」出發而非乾淨狀態、推算的數字要明說是推算、沒設定的預設值也是配置（nginx 的 60 秒）、現成的答案不要丟掉（錯誤訊息裡就有答案）、測試資料的規模要貼近真實負載、不要用 shell 做批次文字替換。這六條的完整脈絡見 git history 中 `2626508` 版本的本檔第 5 節。

---

## 6. 資源位置

**本 repo 的權威文件**：

- `docs/api/asr.md` — ASR 消費端契約，**全文皆為已實作行為**
- `docs/api/tts.md` — TTS 消費端契約，**全文皆為待實作規格**，含 provider 實作檢查清單
- `docs/superpowers/specs/2026-08-05-voxcpm2-serving-transport.md` — 傳輸選型，逐行讀原始碼取證，九項缺口附查證方式
- `docs/superpowers/specs/2026-08-05-tts-text-frontend-tn-g2p.md` — ttsfrd 判定與 TN／G2P 選型，十三項缺口
- `docs/superpowers/specs/2026-08-05-voxcpm2-style-control-measured.md` — 風格控制實測，含雜訊底線與量測失敗紀錄
- `docs/adr/0004-word-level-forced-alignment.md` — 字級對齊的決策與 GPU 實測數據
- `CONTEXT.md` — 領域詞彙

**ADR-0001 標 `superseded`**，不改內文，取代它的新 ADR 是 #19。

**spike harness**：`spike/voxcpm-tts` 分支（真人參考音、GPU）。本 session 另做了 CPU 版的 design／clone／style／affect 四支 spike，在 scratchpad 未入 repo。

**遠端 GPU 機**：`http://10.2.66.102:8088`。只開 HTTP 8088，無 shell 存取。

---

## 7. 唯一沒隔離的變項

`2026-08-05-voxcpm2-style-control-measured.md` §5：**所有風格控制的實測都用合成參考音**（zero-shot 產物，平板）。

情緒在 voice cloning 中從參考音繼承表現力，故「情緒標籤無效」有兩種解釋，本 session 無法區分：模型的通道本身不處理情緒標籤，或通道有效但合成參考音沒有情感範圍可供調度。

**唯一的正面證據不是本 session 測的**：spike #12 以真人台灣參考音在情緒項 PASS。

關閉方式：一段有表情的真人台灣錄音（5 至 30 秒）當參考音，重跑那五組。#43（麥克風錄音）一做出來就能取得這個素材。
