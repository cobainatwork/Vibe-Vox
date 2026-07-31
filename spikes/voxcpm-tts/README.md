# VoxCPM2 spike harness（Task #10 → spike #11 / #12）

在目標 48GB GPU 機上跑，產出音檔供操作者「聽」判定兩道 spike。此為 HITL：agent 備 harness，實跑與聽感判定在你這端。

關聯：wayfinder map #9；解封 #11（台灣口語化）、#12（Controllable 模式音色相似度）。
API/規格依據：`docs/superpowers/specs/2026-07-24-voxcpm-evaluation.md`。

## 1. 安裝（GPU 機）

```bash
pip install voxcpm soundfile librosa
# torch/CUDA 依你的 GPU 環境；VoxCPM2 權重約 8GB，首次執行自 HuggingFace 下載 openbmb/VoxCPM2
```

## 2. 準備素材

- `taiwan_ref.wav`：一段**乾淨的台灣人聲**，5–30 秒，wav/flac/mp3 皆可。口音要明顯台灣、口語自然（這決定 #11 的上限）。
- 選填：該參考音的逐字稿字串（給 Hi-Fi 對照組用，判 #12 的音色相似度上限）。

## 3. 執行

```bash
python spike_voxcpm.py --ref taiwan_ref.wav --ref-text "參考音的逐字稿"
```

輸出在 `out/`：`ctrl_00..04.wav`（Controllable，逐句不同情緒）、`hifi_00.wav`（Hi-Fi 對照）、`ctrl_00_24k_mono_16bit.wav`（adapter 取樣率健檢），外加一張 RTF/延遲表。

## 4. 跑前先確認兩件事（評估文件標為「可信度中」，勿照單全收）

1. `model.generate()` 的回傳型別與原生取樣率 — VoxCPM2 應為 48kHz 的 1D numpy 波形；若你裝的是 0.5B(16k)/1.5(44.1k) 請加 `--sr`。
2. Hi-Fi 模式的參數名（本檔用 `prompt_wav_path` + `prompt_text`）— 對照 <https://voxcpm.readthedocs.io/en/latest/usage_guide.html>。

## 5. 判定 rubric（你聽，agent 不代聽）

### spike #11 — 台灣口語化（pass/fail）
逐句聽 `ctrl_*.wav`，重點：
- 破音字：垃圾唸 **lè sè**（非 lā jī）、和唸 **hàn**、企業 **qì yè**、星期 **xīng qí**。
- 無兒化音；語氣詞（啦／喔／齁／欸）自然。
- 整體節奏、語調聽起來像台灣人講話。
- 判定：全數自然 → **pass（(B) 過）**；被大陸普通話發音/腔調蓋過 → **fail**。

### spike #12 — Controllable 模式音色相似度 + 情緒（pass/fail）
1. `ctrl_*.wav` 的音色像不像 `taiwan_ref.wav` 本人？
2. 五句情緒聽得出逐句差異嗎（cheerful / angry / gentle / excited）？
3. `hifi_00.wav` vs `ctrl_00.wav`：Controllable 用「情緒」換掉的那點「音色保真」，差多少？可接受嗎？
- 判定：音色夠像 + 情緒有效 + 取捨可接受 → **pass（(A) 過）**；否則 **fail**。

### 順帶（延遲 fog）
記下表中 Controllable 的 RTF 與單句生成時間，供 map 的 Not-yet-specified 延遲評估。

## 6. 回報

把兩道判定貼回 issue #11 / #12（或告訴我），我來 work 那兩張票：記錄決議、關閉、更新 map #9 的 Decisions-so-far。兩道皆 pass → 專案 go、引擎定案 VoxCPM。
