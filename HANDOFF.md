# Vibe-Vox 交接文件

**日期**：2026-08-07
**分支**：main @ `9349fb5`（工作樹乾淨，已 push）
**範圍**：#6 TTS 合成路徑打通（`93effa0`）。前一 session 的 TTS 引擎決策落地與 VoxCPM2 實測見第 2 節。

---

## 0. 一句話現況

**#6 把合成路徑從端點一路接到了真實的 vLLM-Omni**：adapter、契約補齊、`docker/tts.Dockerfile`、TTS 測試頁與音色試聽都在了，九條驗收項全數完成。

**但仍然沒有一次真的合成過。** 整條路徑的每一段都有測試，卻沒有一段跑過真的 GPU——tts 服務從未啟動、Dockerfile 從未 build 過、三個模型能否共存也還沒量（#31）。**#6 刻意不關票**，標籤改 `ready-for-human`：剩下的不是實作，是要有人去起服務並用耳朵與 `nvidia-smi` 驗。

**下一步是把它起起來，而不是再往上疊功能。**

TTS 引擎變更的七張決策票解掉三張（#14 #15 #16），剩 #17–#20。

---

## 1. 接手第一件事：把 tts 服務起起來

```
docker compose --profile tts up tts
```

然後四件事，順序有意義：

1. **確認 image build 得起來。** `docker/tts.Dockerfile` 從未跑過。`vllm/vllm-omni:v0.24.0` 是 Docker Hub 上實際存在的最新版本 tag（官方安裝文件的範例寫 v0.26.0，但那個 tag 尚未發布，文件領先了 registry）。**官方 image 是否已含 `voxcpm` 套件未查證**，故 Dockerfile 自行 `pip install voxcpm soundfile ninja`——若 image 本來就有，那幾行是冗餘但無害；若沒有而我漏了別的相依，服務會起不來。
2. **量 VoxCPM2 的實際 VRAM。** 帶 `gpu_uuid`，且**等它完全啟動後**才有意義（啟動途中的讀數會低估數 GB，前一 session 踩過）。compose 的 `--gpu-memory-utilization 0.17` 與 `--kv-cache-memory-bytes 1 GiB` 是**未實測的保守起點**，一定要照實測改 → #31。
3. **打一次 `POST /api/tts/speech`。** 確認端點真的回 48 kHz、降採樣後聽起來正常、Instruction 在真模型上真的有作用。
4. **量首音延遲與 RTF。** #17 才問得到我們自己的系統。

### 提交流程的兩個坑

專案慣例是提交前跑 clean-code 與 code-review，有 pre-commit gate 擋著（`~/.claude/hooks/require-review-before-commit.ps1`）。兩件實測：

- **同一輪內先 invoke skill 再 commit 一定被擋**，hook 讀 transcript 的時機早於該輪寫入。要分兩輪。
- **被擋的嘗試可能被誤判為新的 window 起點。** gate 判定「這次被擋了不算」的方式是看該 commit 行之後 **4 行內**有沒有 deny 訊息；超出 4 行就會把那次失敗的嘗試當成 `lastCommit`，於是先前的 skill invoke 全被排除在窗口外。本 session 因此來回三次。**可行的走法是：在最後一次被擋之後才跑審查，下一輪再提交。**
- 純文件改動可以請使用者放行，但 gate 是 PreToolUse deny、看不到放行，最快是請他自己打 `! git commit ... && git push`。

---

## 2. 動之前先讀完的三條線

### 2.1 已實作 ≠ 已驗證

#6 交付後整條路徑的程式碼都在了，**但沒有任何一次合成經過真的模型**。所有 TTS 行為的依據仍是逐行讀 vLLM-Omni 原始碼取得的。

`docs/api/tts.md` 現在**逐項標示實作狀態**（原本全文標「尚未實作」）。三個缺口寫在文件開頭：分塊串流與 mp3 帶了回 400、TN + G2P 前處理層是靜默的品質落差。

端點會不會真的收到 48 kHz、Instruction 在真模型上是否生效、降採樣後聽起來如何——這三件事都要等服務起來。

### 2.2 Instruction 要寫聲學描述，寫情緒名稱沒有效果

前一 session 的實測，是**唯一一項推翻既有契約敘述**的。完整數據見
`docs/superpowers/specs/2026-08-05-voxcpm2-style-control-measured.md`。

先建立雜訊底線（同輸入三次）：**長度 ×1.18、音量 ×1.32**。沒有這把尺，前兩輪測試的結論全都不可解讀。

| 前綴 | 長度 | 音量 | 判定 |
|---|---|---|---|
| `(生氣、大聲吼、語速很快)` | ×1.88 | ×1.55 | 遠超雜訊，聽感明顯 |
| `(生氣)` | ×1.05 | ×1.36 | 雜訊邊緣 |
| `(開心)`／`(難過)`／`(不耐煩)` | ×1.00–1.15 | ×1.01–1.17 | **全在雜訊內** |

使用者聽感：極端聲學對比「明顯」，五個情緒詞「差別不大」。**中文前綴強於英文**（1.55／1.88 對 1.37／1.50），兩個指標一致。

已據此改掉 `docs/api/tts.md` 的 `instruct` 說明與四個範例、`CONTEXT.md` 的 Instruction 詞條、`docs/spec.md` 的 US29 與範例清單要求、前端面板的說明文字。

**這組數字是在合成參考音上量的**，未隔離的變項見第 7 節。

### 2.3 定版是功能上的必要條件，不是效能優化

zero-shot 同一段描述三次：長度 4.32／2.56／3.36 秒，**離散 69%**，使用者聽感是**三個不同的人**。

帶參考音（reference clone）三次：**離散 18.2%**，聽感是**同一個人、速度些微差異**。

所以 design 音色若在合成時才從描述重生，同一段對話會逐句換人。ADR-0002 的「建立時定版」沒有它，多句對話不可用。

---

## 3. 本 session 完成（`93effa0`、`9349fb5`）

### 3.1 #6 的九條驗收項

`TtsClient.synthesize()`、`adapters/vllm_omni_tts.py`、`StubTtsClient.synthesize()`、`POST /api/tts/speech`、48 kHz → 24 kHz 降採樣、compose 的 tts 服務、`TtsPanel`、音色試聽、測試。逐條對照見 #6 的 comment。

**測試**：BFF 194 passed / 5 skipped（前一 session 是 165/4）、frontend 59 passed（前一 session 49）、typecheck 與 ruff 乾淨、`docker compose config` 通過。

### 3.2 三項與直覺相反的端點行為，全部靠原始碼取證

- **不送 `ref_text`。** 給了會落到 continuation（Hi-Fi）模式，行內風格失效。只給 `ref_audio` 才是 reference（Controllable）模式。
- **不送 `instructions` 與 `task_type`。** 兩者對 VoxCPM2 從未被讀取且不報錯——帶了會回 200 加一段沒套用該風格的音訊。
- **`voice` 恆為 `"default"`。** VoxCPM2 沒有內建語者，該欄位是 OpenAI schema 的必填項但模型語意上忽略它。

### 3.3 控制語法中性化下沉為型別的不變量

`instruct` 由 adapter 組成行內 `(...)` 前綴併入同一個字串，所以未中性化的括號能讓使用者文字變成語氣指令，或讓 `instruct` 的右括號跳出自己的前綴。

中性化原本在端點層，後來下沉到 `adapters/base.py` 的 `Utterance` validator，並加 `frozen=True, validate_assignment=True`——沒有那兩個設定，`u.text = "(evil)"`、`model_construct()`、`model_copy(update=...)` 三條路都繞過 validator，而 docstring 宣稱的保證範圍會比實際大。

同時剝除 `<|...|>` 特殊 token 標記。上游是 `tokenizer.encode(add_special_tokens=True)` 直吃我們送的字串，原樣通過會讓模型看到控制訊號而非字面內容。**ASR 側的 Hotword 清洗早就這樣防了**（`hotword_text.sanitize_text`），TTS 走同一類通道卻漏了。

### 3.4 降採樣走 ffmpeg 管線，本機另以 imageio-ffmpeg 實跑驗證

48 kHz → 24 kHz／mono／16-bit，全程 pipe 不落磁碟。相同規格時跳過轉碼——那不是為測試開的後門，避免無謂的重編碼與子進程往返本身就是對的。

本機無 ffmpeg 使該測試 skip，所以另外借 `imageio-ffmpeg` 的 binary 實跑一次：440 Hz 正弦 48000 frames → 24000 frames，RMS 守恆（14141.7 → 14141.7）。**不把「本機測不到」當成「應該會對」。**

### 3.5 `9349fb5` 更正 agent skills 設定的三處錯誤記載

`docs/agents/` 的三份設定檔曾被加上「該 skill 未安裝」的註記，那是錯的——見 §5.9。

---

## 4. 下一步

### 4.1 #6 的三個缺口（不屬本票驗收，但影響輸出品質）

1. **TN + G2P 前處理層**（`tts_text.py` 目前只有控制語法中性化與特殊 token 剝除）。#15 已把管線定死到不需再 grill 的程度：`raw 繁中 → 淨化 {} → wetext TN + 台灣規則補丁 → OpenCC s2tw → g2pW（只鎖差異字）→ VoxCPM2(normalize=False)`，順序不可倒置。但**沒有票**。沒有它，數字唸法與破音字落到 zh-CN 的行為，而且不會報錯。

   **實作時有個陷阱寫在 `tts_text.py` 的註解裡**：WeTextProcessing 的 `full_to_half` 預設為 `True`，而中性化的做法是把半形括號轉全形——TN 一接上就會把它折回半形，整道安全邊界失效。屆時要嘛關掉 `full_to_half`，要嘛改成刪除而非轉全形。同理，Unicode 相容等價的括號變體（U+FE59、U+207D、U+208D、U+FE35 等，NFKC 都折回半形）目前刻意不處理，因為上游不做正規化——TN 接上後就會。

2. **分塊串流**（回 400 `STREAM_UNSUPPORTED`）。ADR-0003 第 20 行「TTS 串流回應納入範圍」已加註實作狀態，**決策未撤回**。#17 的延遲 bar 未定，且串流實作後才談得上首音延遲。
3. **mp3 輸出**（回 400 `UNSUPPORTED_RESPONSE_FORMAT`）。需要編碼器。

另有一項已知殘留：FastAPI 對每個帶 body 的端點自動宣告 422，而 `main.py` 一律轉 400，那個 422 永遠不會發生。`openapi_extra` 是合併不是取代，拿不掉；要修得動所有端點，不屬 #6。

### 4.2 剩餘的決策票

`#17` 延遲 bar（需要跑起來的服務才量得到）、`#18` 消費端契約重定、`#19` 新 ADR 取代 ADR-0001、`#20` CONTEXT 的 TTS 詞彙。

`#19` 已登記三項 ADR-0001 的既有衝突。`#20` 待決的只剩 Instruction 是否改名、模式用語要不要入詞彙表。

**`#18` 已幾乎是空票**：四個子問題有三個被 #14／#16 答掉（逐句情緒走行內前綴、ID 方案含已不存在的 preset 型別、模式固定 Controllable 不進契約），實質未決的只剩「降採樣落在哪一端」，而 #6 已經把它放在 adapter。

`/wayfinder` 與 `/grill-with-docs` **都有安裝**（見 §5.9），#13 那張 map 可以續接。

### 4.3 其他

`#44`（由 #6 的 review 分出）**參考音時長未在建立時驗證**：端點強制 1.0–30.0 秒，我方三層都沒擋，超界的音色每次合成都回 502 `TTS_UNAVAILABLE`——而契約把該碼標為可重試，消費端會退避重試一個永久失敗。順帶發現 `admin_voices.py` 完全沒呼叫 `detect_audio_format`，任意檔案都存得進音色目錄。

`#8` design 建立（定版）、`#43` 麥克風錄音、`#7` 的更換參考音與改逐字稿、`#31` `#32` `#39` `#40` `#41` 未動。

---

## 5. 給接手者的警告

### 5.1 讀來的不是知道的

前一 session 最大的問題是把官方文件當成驗證。使用者原話：「你都不確定，那你為什麼會確定」「你只照本宣科，有什麼功能、有什麼限制、有什麼界線，你都不知道，你就宣稱答案了」。

具體犯法三種，逐一記著因為它們會重演：

**讀文件當驗證。** 官方 README 列了三種模式，就宣稱「走 design，不需要錄音」。實際上 **Voice design 從來沒被任何 spike 測過**——#11 測台灣讀音、#12 測情緒 vs 保真，兩者都用 clone 路徑。

**讀原始碼但讀錯版本。** `VoxCPM._generate` 在 GitHub `main` 有 `seed` 參數，**發行版 `voxcpm 2.0.3` 沒有**（實跑得到 `TypeError`）。**`main` 的 HEAD 不等於會裝到的東西。**

**測錯層。** 用 `voxcpm` PyPI 套件測，但 production 走 vLLM-Omni 自己的實作，根本不呼叫那個套件。

### 5.2 沒有雜訊基線的量測不可解讀

連跑兩輪 spike 才想到要建雜訊底線。在那之前，`(生氣)` 的音量 ×1.36 一度被當成訊號——它其實貼在 ×1.32 的雜訊邊緣。

**同一輸入跑三次先量離散度，再解讀任何差異。**

同一輪還有一個量測直接失效：`torchaudio.functional.detect_pitch_frequency` 在 48 kHz 上以預設參數量到 580–725 Hz、標準差 900 Hz，而人聲基頻約 85–255 Hz、標準差不可能大於平均。**指標本身要先驗證量得到你要的東西。**

### 5.3 不要把答案寫進問題裡

測情緒時用的提示詞是「生氣、**大聲吼、語速很快**」，量到音量與語速改變就差點當成情緒生效。那是自己要求的東西。拿掉聲學提示、只給情緒詞重測，才得到真正的答案。

### 5.4 註解不能宣稱不存在的機制

三次了，每次形狀不同：

1. `repository.delete` 的 docstring 寫「實體檔留給清理程序回收」，而**那個回收者不存在**（`cleanup.py` 只掃 `temp_dir`）。刪音色會永久洩漏磁碟，而註解讓它看起來有人管。
2. #6 的前端加了 `eslint-disable-next-line jsx-a11y/media-has-caption`，而**這個 repo 沒有 eslint 設定**。
3. `Utterance` 的 docstring 寫「中性化由本型別保證」，但沒有 `frozen`／`validate_assignment` 時**三條路都繞得過**。安全審查實測出來的。

**寫「由 X 處理」之前，先確認 X 存在，而且確認它涵蓋你宣稱的範圍。**

### 5.5 使用者的耳朵與數字是兩把不同的尺

RMS 與長度量得到韻律，**量不到情感**。使用者說「不能很肯定是不是生氣，只是聲音大、語速快」——那是數字碰不到的一層。反過來，使用者聽感也需要數字校準：「中文對比比較大」對應到 1.55／1.88 對 1.37／1.50。

### 5.6 問句不是工單

使用者問「clone 有做麥克風錄音嗎」，答完就直接開始寫測試，被質問才回頭補票——順序完全反了。**要做功能就開票。**

### 5.7 沒做出來的東西不要為它寫驗證

曾為「參考音時長 1.0–30.0 秒」開票（#42，已關），而當時合成端點不存在、TTS 服務沒部署，**從來沒撞到過**。使用者原話：「你功能沒做出來怎麼驗證，一直在空跑，猜測」。

**先讓路徑跑通，有實際的失敗樣態再設計驗證。** 註記：#6 交付後路徑通了，同一件事以 #44 重新開票——這次有具體的失敗樣態（502 被標成可重試）。

### 5.8 審查跑到一半斷掉不等於審過

#6 的第二輪 code-review 回報「No findings survived verification」，看起來乾淨。實際的 stats 是 `candidates: 61, verified: 0`——61 個候選缺陷產出後，47 個 verifier **全部因週限額失敗**（55 個 agent 只有 7 個完成）。

那不是「審過沒問題」，是「審到一半斷電」。重跑之後兩軸各找出四項實質缺陷，其中兩項會實際咬人（參考音時長 → #44、data URI 謊報容器）。

**看 stats 不要只看結論。** `verified: 0` 配 `candidates: 61` 是失效的訊號，不是好消息。

### 5.9 available skills 清單不是安裝清單

本 session 從 agent 的 available skills 清單推論 `/wayfinder`、`/grill-with-docs`、`/triage` 未安裝，據此在 `docs/agents/` 三份設定檔加了「該 skill 未安裝」註記，並在建議流程時排除了 `/wayfinder`。

**全都是錯的。** 實際檢查磁碟，那七個 skill（另含 `to-spec`、`to-tickets`、`implement`、`improve-codebase-architecture`）在 `1.2.0` 與 `1.2.2` 都存在。使用者直接指正：「這個 /grill-with-docs 一定有，只是agent呼叫不起來而已吧」。

**那份清單只是 agent 能主動 invoke 的子集**，使用者以 `/plugin:skill` 叫得起清單外的。本 session 的 `/mattpocock-skills:implement` 就是實例——它不在清單上，被叫起來了。

同一個錯誤更早就寫進 #13 的 Notes（已留 comment 更正），害那張 map 的 index 一直手動維護。

**要判斷 skill 在不在，看 `~/.claude/plugins/cache/<owner>/<plugin>/<version>/skills/`。** 反過來也成立：清單裡沒有不代表使用者叫不動，所以在建議流程時不要因為「我叫不起來」就排除某條路徑——那是 agent 的限制，不是環境的限制。

### 5.10 前面幾輪的教訓仍然有效

驗證要從「既有狀態」出發而非乾淨狀態、推算的數字要明說是推算、沒設定的預設值也是配置（nginx 的 60 秒）、現成的答案不要丟掉（錯誤訊息裡就有答案）、測試資料的規模要貼近真實負載、不要用 shell 做批次文字替換。這六條的完整脈絡見 git history 中 `2626508` 版本的本檔第 5 節。

---

## 6. 資源位置

**本 repo 的權威文件**：

- `docs/api/asr.md` — ASR 消費端契約，**全文皆為已實作行為**
- `docs/api/tts.md` — TTS 消費端契約，**逐項標示實作狀態**（三個缺口寫在文件開頭）
- `docs/superpowers/specs/2026-08-05-voxcpm2-serving-transport.md` — 傳輸選型，逐行讀原始碼取證，九項缺口附查證方式
- `docs/superpowers/specs/2026-08-05-tts-text-frontend-tn-g2p.md` — ttsfrd 判定與 TN／G2P 選型，十三項缺口
- `docs/superpowers/specs/2026-08-05-voxcpm2-style-control-measured.md` — 風格控制實測，含雜訊底線與量測失敗紀錄
- `docs/adr/0004-word-level-forced-alignment.md` — 字級對齊的決策與 GPU 實測數據
- `CONTEXT.md` — 領域詞彙（#6 新增 `Utterance` 詞條）

**ADR-0001 標 `superseded`**，不改內文，取代它的新 ADR 是 #19。
**ADR-0003 的串流條目已加註實作狀態**，決策本身未撤回。

**spike harness**：`spike/voxcpm-tts` 分支（真人參考音、GPU）。前一 session 另做了 CPU 版的 design／clone／style／affect 四支 spike，在 scratchpad 未入 repo。

**遠端 GPU 機**：`http://10.2.66.102:8088`。只開 HTTP 8088，無 shell 存取。

---

## 7. 唯一沒隔離的變項

`2026-08-05-voxcpm2-style-control-measured.md` §5：**所有風格控制的實測都用合成參考音**（zero-shot 產物，平板）。

情緒在 voice cloning 中從參考音繼承表現力，故「情緒標籤無效」有兩種解釋，無法區分：模型的通道本身不處理情緒標籤，或通道有效但合成參考音沒有情感範圍可供調度。

**唯一的正面證據不是這兩個 session 測的**：spike #12 以真人台灣參考音在情緒項 PASS。

關閉方式：一段有表情的真人台灣錄音（5 至 30 秒）當參考音，重跑那五組。#43（麥克風錄音）一做出來就能取得這個素材——而 tts 服務起來之後，這件事就只差那段錄音。
