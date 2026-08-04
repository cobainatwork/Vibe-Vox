# 字級時間戳改由獨立 forced aligner 產生

## Status

accepted

## Context

消費端 AI_practise 需要字級時間戳，用途明確：由 AI_practise 自訂閾值判定停頓，再以**停頓佔全文的百分比**評判學員話術的流暢度。

VibeVoice-ASR 自身的時間戳無法支撐此用途。它的分段語義是**窮盡連續切分**而非語音活動區段：訓練標註（`finetuning-asr/toy_dataset/`）第一段自 `0.0` 起、末段 `end` 等於 `audio_duration`，相鄰段間隙 0–0.66 秒，19 個間隙中 6 個精確為 0。實測單語者連續語音時 9 個間隙全部為 0。段界是模型自選的切點，不是發音結束時刻，**停頓資訊在這個表示法裡不存在**——不是精度不足，是資訊本身不存在。

時間戳亦非對齊演算法計算，而是模型 autoregressive 生成的數字 token。

## Decision

新增 **Qwen3-ForcedAligner-0.6B**（實際 0.9B 參數，Apache-2.0）為獨立部署單元，以 transformers backend 常駐，不併入現有 vLLM 容器。

```
音檔 → AudioIntake.transcoded()
     → vllm (VibeVoice-ASR)  → segments：Content + 切點 Start/End + Speaker
     → aligner                → 逐段字級 Start/End（單請求內 batch 送全部段落）
     → BFF 合併 + 合理性檢查   → segments[].words[] + 對齊狀態 + 彙總數字
```

VibeVoice 的切點時間戳降級為**切片依據**：段長 30–40 秒遠低於 aligner 的 **180 秒**上限，逐段對齊使單段對歪不污染其他段。切片左右各留 buffer 吸收漂移，對齊後扣除。

上限值以 `qwen-asr` 原始碼為準（`qwen_asr/inference/utils.py` 的 `MAX_FORCE_ALIGN_INPUT_SECONDS = 180`），非 model card 宣稱的 5 分鐘。套件本身不強制檢查，逾限會靜默對歪，故由 aligner 服務擋下。

以下六項經與消費端負責人逐項確認：

**對齊單位為單一漢字**，非語意上的詞。強制對齊的時間槽位由我們決定，選字級即不需引入中文斷詞器——斷詞本身有錯誤率且會直接扭曲時間戳歸屬。字級資訊是詞級的超集，消費端要聚合隨時可為。停頓偵測不依賴詞邊界。

**Segment 的 `Start`／`End` 以字級時間戳重算**（首字 Start、末字 End）。原切點語義為空且已實際誤導判讀；不重算則同一回應內段層與字層時間語義互相矛盾。重算後段間間隙即句間停頓，對評分直接可用。

**停頓判定不在本系統定義。** 供應端只給時間戳，閾值與評分規則屬 AI_practise 業務邏輯。停頓閾值不是語音學常數，會隨話術類型與學員程度調整；固化於 ASR 端將使每次調整評分標準都需改服務並重新部署。

**對齊失敗採兩層降級，且必為顯式標記。** 單段未通過合理性檢查（單字時長異常、時間戳逆轉、段內總長與音訊偏離）→ 該段回退切點時間戳、Word 清單空、標記對齊狀態，其餘段照常。aligner 服務不可用 → ASR 結果照常回傳，全段標記未對齊。逐字稿有獨立價值，不因評分這項附加功能失效而一併不可得。狀態必須是明確欄位，不以「Word 清單為空」隱含表示——空清單會被誤讀為「該段沒有字」。

**回應附四個彙總數字**：音檔總長、首字 Start、末字 End、已對齊時長。用途是讓 AI_practise 能自行組出正確分母。系統**不預先排除**任何區間：開頭沉默（不敢開口）本身即話術缺失，排除它等於開後門讓學員可先發呆再開始；結尾沉默則存在「講完忘記按停止」與「講不下去」兩種語義，音訊上無從區分，該規則只能由消費端依其他訊號決定。學員全程未發話時，欄位結構仍完整回傳（值為 null 或 0）而非報錯。

**管理平面僅做最小驗證**：ASR 測試頁顯示對齊狀態，字級數字摺疊備查。不做時間軸視覺化與逐字試聽——對齊準確度的權威驗證應以標註資料集比對，而實際會發生的故障（整段對歪、時間戳歸零、順序逆轉）看數字即可辨識。

## Considered Options

- **沿用 VibeVoice 切點時間戳**：否決。停頓資訊不存在於窮盡切分表示法中，無論精度如何都無法支撐流暢度評分。
- **WhisperX / NeMo Forced Aligner**：否決。累積平均位移 92.1ms / 107.5ms，Qwen3-ForcedAligner 為 37.5ms（英文）、33.1ms（中文），相對降低 67–77%。
- **併入現有 vllm 容器**：否決。該 image pin vLLM v0.14.1、bake 7B 權重、附官方 plugin，是系統最脆弱且重建最慢的部分。官方 benchmark 本身即以 transformers 執行，理由是「與 vLLM 速度差異不大」，故無收益。
- **按需載入、用完釋放**：否決。原為應付 VRAM 吃緊而設想，48GB 實際有餘裕，常駐更簡單且無冷啟延遲。
- **詞級對齊**：否決。需引入中文斷詞器，多一錯誤源，且資訊量少於字級。
- **由供應端輸出停頓標記**：否決。理由見上。
- **`words` 設為請求時可選開關**：否決。可省下約 700 KB／小時的 payload，但為契約增加一個參數與兩條測試路徑，而消費端每次都需要。

## Consequences

- **VRAM 帳已由 #26 實測取代，且推翻本節原有的三項前提**。原文假設「單張 RTX 6000 Ada 48GB，vLLM 26–29 GB（`gpu_memory_utilization` 0.55–0.6）+ VoxCPM2 約 8 GB + aligner 3–4 GB = 37–41 GB，餘裕 7–11 GB」。2026-08-04 於遠端機實測：

  | 項目 | 原假設 | 實測 |
  |---|---|---|
  | GPU 數量與容量 | 單張 48 GB | **兩張**，各 46068 MiB |
  | vLLM | 26–29 GB（utilization 0.55–0.6） | **37890 MiB**（utilization 0.8） |
  | aligner 峰值 | 3–4 GB（估算） | **2348 MiB**（idle 2186，單段對齊 +162） |
  | GPU 0 餘裕 | 7–11 GB | **5830 MiB** |

  三項修正：

  **`gpu_memory_utilization` 0.55–0.6 從未被實作。** `docker/vllm.Dockerfile` 直接跑官方 `vllm_plugin/scripts/start_server.py`，該腳本的預設為 `0.8`，未被覆寫，故 vLLM 實際佔 46068 × 0.8 ≈ 36854 MiB（實測 37890，差額為 CUDA context 開銷）。

  **機器有兩張卡，第二張已被非 Vibe-Vox 的工作負載佔用。** GPU 1 由 gpustack 管理的 `qwen3.6-35b-a3b` 與 `gemma-4-12b-it-qat` 佔 33118 MiB（餘 12934 MiB），且為動態調度，不可假設其餘裕穩定。`docker-compose.yml` 目前把三個 GPU 服務全釘在 `device_ids: ["0"]`。

  **VoxCPM2 在當前配置下無法與 vLLM、aligner 並存於 GPU 0**：餘裕 5830 MiB 小於其所需的約 8 GB。aligner 不是瓶頸（實測比估算少 1–2 GB），瓶頸是未設定的 vLLM utilization。此為 TTS 上線的前置阻礙，須先決定是把 vLLM 調回原設計的 0.55–0.6（代價為 KV cache 縮小、影響併發與 `max_model_len`），或改變卡的分配。
- **ASR 文字品質成為時間戳品質的前置條件**。強制對齊無容錯機制（訓練以 MFA pseudo-label，假設 text 與 audio 完全對應），轉錄若含亂碼、漏字、多字會靜默對歪。故 #23（prompt 對齊訓練格式）由可選優化升格為必要前置。
- **時間解析度 80ms**（`config.json` 的 `timestamp_segment_time`；模型輸出的離散索引乘以此值即毫秒）。可穩定分辨的最小停頓約 80–160ms；話術評分關心的猶豫、卡頓多在 300ms 以上，故足夠。80ms 以下的微停頓測不出，該尺度屬協同發音範疇，本不應計入評分。
- **回應體積顯著增加**。中文語速約每分鐘 200–300 字，60 分鐘音檔產生逾萬個 Word 物件，約增 700 KB。上傳上限已為 200 MB，此量級無虞，但消費端須知回應不再是小 JSON。
- 模型有兩種發布形式，**採 `Qwen3-ForcedAligner-0.6B` 走 `qwen-asr` 套件**（#26 定案）。`-hf` 變體走 transformers 的 `AutoModelForTokenClassification`，但在官方 Transformers release 納入前需自 git 安裝 transformers，版本釘不住、build 不可重現；`qwen-asr` 0.0.6 自身釘死 `transformers==4.57.6`，且為官方 model card 的首選範例。兩者同屬 transformers backend，此選擇不改變本決策。權重預抓後 bake 進 image，與 vllm image 同樣做法，離線可跑。
- **標點與符號不產生 Word**。`qwen-asr` 的 `clean_token` 只保留 Unicode 字母、數字與 `'`，其餘字元在送入模型前即被剝除，故 `words` 的數量不等於 `Content` 的字元數。T3 的合理性檢查不可以「兩者相等」為判準，否則每段都會被誤判為對齊失敗。
- **單字時長異常確有發生，T3 的合理性檢查不可省**。#26 以官方測試音訊實測（4.204 秒、13 字）即出現一例：「幾」的 `Start` 與 `End` 相同（零時長），且與下一字「乎」之間有 0.16 秒間隙——「幾乎」為連讀詞，該處不應有停頓。此異常出現在 `qwen-asr` 的 `fix_timestamp` **之後**：該函式以最長遞增子序列修正時間戳的單調性，不修對齊正確性。整體對齊仍可用（其餘字時長 0.16–0.40 秒，符合中文語速），但零時長與虛假間隙是 T3 必須攔下的兩種型態。
- **服務須無狀態**。測試區不實作併發（無跨請求佇列、worker 池），但 prod 確定為多併發架構，故不得使用全域可變狀態或假設獨佔 GPU，屆時加 replica 或補 batch queue 即可，不必重寫。prod 的 VRAM 餘裕會被併發吃掉，需另行重算。
- 連動 ADR-0003：消費端契約擴充 `words`、對齊狀態與四個彙總數字。
- 連動 `CONTEXT.md`：Segment 定義修正（非語句單位），新增 Word、Forced alignment、對齊狀態三個詞條。
