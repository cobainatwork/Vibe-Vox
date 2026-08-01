# VoxCPM2 spike harness（Task #10 → spike #11 / #12）

在目標 48GB GPU 機上跑，產出音檔供操作者「聽」判定兩道 spike。此為 HITL：agent 備 harness，實跑與聽感判定在你這端。

關聯：wayfinder map #9；解封 #11（台灣口語化）、#12（Controllable 模式音色相似度）。
API/規格依據：`docs/superpowers/specs/2026-07-24-voxcpm-evaluation.md`。

## 1. 前置（GPU 機，只需 docker）

不必在 host pip。只要 host 有 `docker` 與 `nvidia-container-toolkit`（本專案既有）。所有依賴（voxcpm/torch/torchaudio）在 build 時裝進 image。

```bash
git fetch origin && git checkout spike/voxcpm-tts
cd spikes/voxcpm-tts
```

## 2. 準備素材（放進 `spikes/voxcpm-tts/` 資料夾即可）

- **參考音**：一段乾淨的台灣人聲 `.wav`（5–30 秒；口音要明顯台灣、口語自然，這決定 #11 的上限）。檔名隨意，例如 `taiwan_ref.wav`。
- **逐字稿（選填，是檔案不是打字）**：把參考音「講的內容」用記事本存成**同名 `.txt`**（`taiwan_ref.wav` → `taiwan_ref.txt`，UTF-8）。有它才跑 Hi-Fi 對照組（判 #12 音色相似度上限）；沒有就只跑 Controllable，不影響 #11。你不必在指令裡打任何文字。

## 3. 執行（Docker）

參考音（和選填的同名 `.txt`）放好後，直接：

```bash
./run.sh
```

會自動抓資料夾裡的 `.wav`（有多個時再指定 `./run.sh 檔名.wav`）。`run.sh` 會：build image → 跑 GPU 煙霧測試（須印 `cuda.is_available = True`，否則結果不可信）→ 執行 spike。輸出在 `./out/`：`ctrl_00..04.wav`（Controllable 逐句情緒）、`hifi_00.wav`（Hi-Fi 對照，需有 `.txt` 才產出）、`ctrl_00_24k_mono_16bit.wav`（adapter 取樣率健檢），外加 RTF/延遲表。HF 權重快取存於具名 volume `voxcpm-hf-cache`，約 8GB 只下一次。

不想用 `run.sh` 就手動：

```bash
docker build -t voxcpm-spike:latest .
docker run --rm --gpus all -v "$PWD":/work -v voxcpm-hf-cache:/hf-cache \
  voxcpm-spike:latest --ref /work/taiwan_ref.wav --ref-text-file /work/taiwan_ref.txt
```

Fallback（不用 Docker）：`pip install voxcpm soundfile librosa` 後 `python spike_voxcpm.py --ref taiwan_ref.wav --ref-text-file taiwan_ref.txt`。

## 4. 跑前先確認兩件事（評估文件標為「可信度中」，勿照單全收）

1. `model.generate()` 的回傳型別與原生取樣率 — VoxCPM2 應為 48kHz 的 1D numpy 波形；若你裝的是 0.5B(16k)/1.5(44.1k) 請加 `--sr`。
2. Hi-Fi 模式的參數名（本檔用 `prompt_wav_path` + `prompt_text`）— 對照 <https://voxcpm.readthedocs.io/en/latest/usage_guide.html>。

## 4b. 版本排錯（build 或 GPU 測試失敗時）

- **已知並已修**：症狀 `cuda.is_available = False` + 「driver too old (found 12080)」，代表容器的 torch 是為比目標機 driver（CUDA 12.8）更新的 CUDA 編譯的。根因：voxcpm 要求 `torch>=2.5.0`，會把過舊的 base torch 升級成最新（預設 CUDA build > 12.8）的 wheel。**已改用 base image `pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime`**（torch 2.6+cu124，滿足 voxcpm 且 cu124 相容 driver>=12.4）。
- 換到 driver CUDA < 12.4 的機器：把 base tag 的 cuda 版本調低對齊 driver（tag 見 <https://hub.docker.com/r/pytorch/pytorch/tags>）。
- 煙霧測試會印 `torch <版本> +cuda <build>`；若 `+cuda` 數字 > `nvidia-smi` 的 CUDA 版本，就是 torch 又被某依賴升級了 — 把 base tag 的 cuda 版本對齊 driver 即可。

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
