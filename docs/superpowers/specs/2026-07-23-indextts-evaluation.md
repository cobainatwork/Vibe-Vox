# 研究：IndexTTS 是否滿足 Vibe-Vox 的 clone + 情緒控制 + 台灣口語化需求

日期：2026-07-23
研究問題：開源專案 IndexTTS（GitHub `index-tts/index-tts`）能否滿足 Vibe-Vox 的三項核心需求，(A) 對「克隆出的音色」做情緒控制且最好可逐句調整、(B) 產出台灣口語化中文語音、(C) 與 VibeVoice-ASR 共存於單張 RTX 6000 Ada 48GB 並包進現行 `TtsClient` adapter 對齊 `/api/tts/speech` 契約。
可信度分級：每條結論後標註來源，區分「官方 primary」與「社群」。無來源佐證者明寫「無來源，無法證實／需實測」，不臆測。

---

## 1. 結論摘要（結論先行）

1. (A) clone + 情緒（可逐句調）：**可行**。
   - IndexTTS2 官方論文明述「achieves disentanglement between emotional expression and speaker identity, enabling independent control over timbre and emotion」，音色來源（`spk_audio_prompt`）與情緒來源（`emo_audio_prompt`／`emo_vector`／`emo_text`）在 API 層完全分離。此即「用 A 的音色講話、情緒由另一路獨立指定」。〔官方 primary〕 https://arxiv.org/abs/2506.21619
   - 逐句（per-utterance）調整：官方未提供「單次呼叫內多情緒」功能，但 `infer()` 為每次呼叫獨立帶入情緒參數，逐句切分後每句一次呼叫即可各自指定情緒；官方 demo 亦展示同一語者逐句切換 angry／cry／fear／happy 等情緒。此為 API 形狀 + demo 佐證的推得結論。〔官方 primary〕 https://index-tts.github.io/index-tts2.github.io/

2. (B) 台灣口語化：**部分／需實測（無官方來源）**。
   - 官方明列語言支援僅中文（zh）與英文（en），未宣稱台灣口音或台灣口語化。〔官方 primary〕 https://huggingface.co/IndexTeam/IndexTTS-2
   - 由 bilibili（中國大陸）以其資料訓練，預設輸出為大陸普通話發音與用詞。zero-shot 參考音可帶入音色與部分韻律／口音，且模型支援拼音（pinyin）覆寫特定字發音，但「台灣口語化語感」取決於參考音與輸入文字寫法，官方無任何依據。**無來源，需實測 spike。**

3. (C) 單 48GB GPU 共存 + 現行 adapter：**可行**。
   - IndexTTS2 權重磁碟總量 5.9 GB（`gpt.pth` 3.48 GB + `s2mel.pth` 1.2 GB 為主），支援 FP16 半精度推論。〔官方 primary〕 https://huggingface.co/IndexTeam/IndexTTS-2/tree/main
   - 社群實測可在 RTX 3060 12GB 完成推論（未 OOM），顯示推論峰值 VRAM < 12GB；該卡慢是算力受限非記憶體受限。〔社群〕 https://github.com/index-tts/index-tts/issues/585
   - vLLM `gpu_memory_utilization` 壓到 0.55-0.6 時，48GB 保留約 26-29GB 給 ASR，剩約 19-22GB；IndexTTS2 推論峰值（FP16 推算 6-10GB，見 6.3）可容納且有餘裕。與 ASR 共存無記憶體衝突。
   - adapter：官方無 HTTP／OpenAI 端點，但 `IndexTTS2.infer()` 直接產 wav，包一層薄 FastAPI 即可對齊 `/api/tts/speech`；需在 adapter 內把原生 22.05kHz 重採樣為 24kHz/mono/16-bit（見 7）。整合成本低。

4. 總判：**應取代 Qwen3-TTS（作為情緒可控 clone 引擎），但落地前須過兩道 spike（台灣口語化、效能/延遲）與一道法務確認（bilibili 授權）**。
   - 依據：Vibe-Vox 既有確認缺口是「Qwen3-TTS 的 clone 路徑不支援 instruct／情緒」（見 sibling note `2026-07-23-qwen3-tts-clone-instruct-research.md`）。IndexTTS2 以 timbre-emotion 解耦直接補上此缺口，且情緒可逐句、可用情緒參考音或文字描述驅動，能力嚴格優於 Qwen3-TTS clone 路徑。
   - 保留條件：授權由 Qwen3-TTS 的 Apache-2.0 變為 bilibili Model Use License Agreement（source-available，非 OSI 開源，見 8）；台灣口語化無官方依據；IndexTTS2 的 GPT 尚無成熟 vLLM 加速（效能風險，見 6.4）。三者未清前，建議先以並存試點驗證再全面取代。

---

## 2. 版本譜系與權威來源（主問題 1）

三個版本同屬一個官方 repo `index-tts/index-tts`，核心團隊明述唯一官方管道即此 repo。〔官方 primary〕 https://github.com/index-tts/index-tts

| 版本 | 官方釋出日 | 定位／能力 | 論文 |
| --- | --- | --- | --- |
| IndexTTS 1.0 | 2025/03/25 | 首度釋出權重與推論碼；GPT-style，基於 XTTS/Tortoise，中文字 + 拼音混合建模、標點控停頓、整合 BigVGAN2 | arXiv:2502.05512 |
| IndexTTS-1.5 | 2025/05/14 | 顯著提升穩定度與英文表現（無獨立新論文，為 1.0 的改進） | 沿用 2502.05512 |
| IndexTTS2 | 2025/09/08 | 首個具「精確合成時長控制」的自回歸 TTS；情緒與音色解耦、多模態情緒輸入 | arXiv:2506.21619（亦收錄於 AAAI） |

版本與釋出日、論文編號來源。〔官方 primary〕 https://github.com/index-tts/index-tts/blob/main/README.md

確切 URL：

1. 官方 repo：https://github.com/index-tts/index-tts 〔官方 primary〕
2. IndexTTS 1.0 論文：https://arxiv.org/abs/2502.05512 〔官方 primary〕
3. IndexTTS2 論文：https://arxiv.org/abs/2506.21619 〔官方 primary〕
4. IndexTTS2 demo page：https://index-tts.github.io/index-tts2.github.io/ 〔官方 primary〕
5. HF 模型卡：
   - v1：https://huggingface.co/IndexTeam/Index-TTS 〔官方 primary〕
   - v1.5：https://huggingface.co/IndexTeam/IndexTTS-1.5 〔官方 primary〕
   - v2：https://huggingface.co/IndexTeam/IndexTTS-2 〔官方 primary〕
6. ModelScope 鏡像：https://modelscope.cn/models/IndexTeam/IndexTTS-2 〔官方 primary〕

本評估以 IndexTTS2 為對象，因情緒控制能力僅 v2 具備。

---

## 3. Voice cloning（主問題 2）

1. 是否 zero-shot：**是**。官方定位即「Zero-Shot Text-To-Speech System」，clone 僅需一段參考音、無需微調。〔官方 primary〕 https://github.com/index-tts/index-tts
2. clone 輸入：**只需參考音（單一 `spk_audio_prompt` wav），不需逐字稿（transcript）**。最小呼叫如下（逐字保留原文）：〔官方 primary〕 https://github.com/index-tts/index-tts/blob/main/README.md

```python
from indextts.infer_v2 import IndexTTS2
tts = IndexTTS2(cfg_path="checkpoints/config.yaml", model_dir="checkpoints")
tts.infer(spk_audio_prompt='examples/voice_01.wav', text=text, output_path="gen.wav")
```

3. 參考音長度與 clone 品質：README 未給明確最短秒數或品質對照表。**無來源，無法證實**（此點需以實際參考音長度做 spike 校準）。

---

## 4. 情緒／風格控制與 timbre-emotion 解耦（主問題 3，本案關鍵）

### 4.1 官方支援情緒控制：是，且提供三種輸入模態

IndexTTS2 情緒輸入有三條路徑，皆與音色來源 `spk_audio_prompt` 分離（逐字保留原文）。〔官方 primary〕 https://github.com/index-tts/index-tts/blob/main/README.md

A. 情緒參考音（emotion reference audio）：

```python
tts.infer(spk_audio_prompt='examples/voice_07.wav', text=text,
          emo_audio_prompt="examples/emo_sad.wav", emo_alpha=0.9)
```

B. 情緒向量（8 維 float，順序固定）：

```python
tts.infer(spk_audio_prompt='examples/09.wav', text=text,
          emo_vector=[0, 0, 0.8, 0, 0, 0, 0, 0], use_random=False)
```

向量順序：`[happy, angry, sad, afraid, disgusted, melancholic, surprised, calm]`。

C. 文字情緒描述（text prompt，經 fine-tuned Qwen3 的 soft instruction 機制）：

```python
tts.infer(spk_audio_prompt='examples/voice_12.wav', text=text,
          emo_alpha=0.6, use_emo_text=True, emo_text=emo_text)
```

關鍵參數：
- `spk_audio_prompt`：音色（timbre）來源參考音。
- `emo_audio_prompt`：情緒來源參考音。
- `emo_alpha`：情緒強度，有效範圍 `0.0 - 1.0`，預設 `1.0`；使用文字情緒模式時官方建議約 `0.6`（或更低）以求自然。〔官方 primary〕 https://raw.githubusercontent.com/index-tts/index-tts/main/README.md
- `emo_vector`：8 維情緒強度向量。
- `use_emo_text` / `emo_text`：啟用文字情緒描述並帶入描述文字。
- `use_random`：是否引入隨機性。

### 4.2 timbre 與 emotion 是否解耦：是（本案最關鍵，直接命中）

論文摘要逐字：「IndexTTS2 achieves disentanglement between emotional expression and speaker identity, enabling independent control over timbre and emotion. In the zero-shot setting, the model can accurately reconstruct the target timbre (from the timbre prompt) while perfectly reproducing the specified emotional tone (from the style prompt).」〔官方 primary〕 https://arxiv.org/abs/2506.21619

demo page 逐字確認三種組合，含「distinct audio prompts as references for timbre and emotional expression, respectively」，即音色與情緒可用兩段不同參考音各自指定。〔官方 primary〕 https://index-tts.github.io/index-tts2.github.io/

判定：Vibe-Vox 需求 (A)「用克隆音色講話、情緒獨立指定」直接由 `spk_audio_prompt`（克隆音色）+ `emo_*`（情緒）達成。這正是 Qwen3-TTS clone 路徑缺的能力。

### 4.3 逐句（per-utterance）調整：可行（推得）

- 官方未宣稱「單次 `infer()` 呼叫內多段不同情緒」的功能，README 無此描述。
- 但 `infer()` 每次呼叫獨立帶入 `emo_*` 與 `emo_alpha`，逐句切分文字、每句一次呼叫即可逐句指定不同情緒與強度。此為 API 形狀的直接推得。〔官方 primary〕 https://github.com/index-tts/index-tts/blob/main/README.md
- demo page 展示同一語者對不同句子輸出 angry／cry／fear／depressed／happy／surprise／calm，佐證逐句情緒切換的可達成性。〔官方 primary〕 https://index-tts.github.io/index-tts2.github.io/
- 相對 Qwen3-TTS clone 的鏈式做法「情緒被固定在單段參考音、無法逐句即時調整」，IndexTTS2 以 `emo_vector`／`emo_text` 逐句傳參即可逐句換情緒，明顯更靈活。

### 4.4 時長控制（duration control）：是

論文逐字：兩種模式，一是「explicitly specifies the number of generated tokens to precisely control speech duration」，二是「freely generates speech in an autoregressive manner without specifying the number of tokens」。demo 展示 0.75x／1.0x／1.25x 時長變化。〔官方 primary〕 https://arxiv.org/abs/2506.21619 、 https://index-tts.github.io/index-tts2.github.io/

---

## 5. 語言支援（主問題 4）

1. 官方明列：**中文（zh）與英文（en）**。HF 模型卡語言標籤為 `en` 與 `zh`；demo 僅示範中、英文；論文評測亦以中英文為主。〔官方 primary〕 https://huggingface.co/IndexTeam/IndexTTS-2 、 https://index-tts.github.io/index-tts2.github.io/
2. 中文機制：中文字與拼音（pinyin）混合建模，可用拼音校正特定字發音、以標點控制停頓。〔官方 primary〕 https://github.com/index-tts/index-tts
3. 台灣口語化：官方**無**任何台灣口音／台灣用詞的宣稱。誠實區分：
   - 「官方明列的語言支援」＝中文（普通話）+ 英文，有來源。
   - 「台灣口語化語感」＝無官方依據。模型由 bilibili 以其資料訓練，預設偏大陸普通話發音與用詞；zero-shot 參考音可帶音色與部分韻律／口音，拼音覆寫可微調個別字音，但整體口語化取決於參考音與輸入文字寫法。**無來源，需實測 spike**（建議以台灣口音參考音 + 台灣口語文字，實測發音、用詞、語調是否可接受）。

---

## 6. 模型大小與 VRAM（部署可行性，主問題 5）

### 6.1 權重磁碟大小（官方）

IndexTTS2 HF repo 檔案與大小（逐字保留）：〔官方 primary〕 https://huggingface.co/IndexTeam/IndexTTS-2/tree/main

| 檔名 | 大小 |
| --- | --- |
| gpt.pth | 3.48 GB |
| s2mel.pth | 1.2 GB |
| feat2.pt | 375 kB |
| feat1.pt | 57.2 kB |
| wav2vec2bert_stats.pt | 9.34 kB |
| bpe.model | 476 kB |
| config.yaml | 2.88 kB |

總 repo 大小 5.9 GB。

### 6.2 模型結構（官方 config.yaml）

GPT 主體：24 層、dim 1280、20 attention heads、text tokens 12000、mel codes 8194。vocoder 為 BigVGAN v2 22kHz（`nvidia/bigvgan_v2_22khz_80band_256x`）。〔官方 primary〕 https://huggingface.co/IndexTeam/IndexTTS-2/raw/main/config.yaml

推論期另需載入 wav2vec2-BERT 語意特徵抽取器、CAMPPlus 語者編碼器、BigVGAN2 vocoder；使用文字情緒路徑時另載入 fine-tuned Qwen3 情緒模型（會額外佔用 VRAM）。

### 6.3 推論 VRAM

1. 官方未給明確 VRAM 數字，僅提供 FP16 半精度推論（更快、更省 VRAM，品質略降）與可選 DeepSpeed。〔官方 primary〕 https://raw.githubusercontent.com/index-tts/index-tts/main/README.md
2. 社群實測可在 RTX 3060 12GB 完成推論、未 OOM，代表推論峰值 VRAM < 12GB。〔社群〕 https://github.com/index-tts/index-tts/issues/585
3. 推算（標示為推算）：以 24 層 / dim 1280 的 GPT（約 0.5B 級）加上 wav2vec2-BERT、BigVGAN2、s2mel 等模組，FP16 推論峰值約 6-10 GB；若同時啟用文字情緒的 Qwen3 模型會再增數 GB。此為依參數量與精度的合理推算，非官方數字。

### 6.4 與 VibeVoice-ASR 共存判定

- vLLM `gpu_memory_utilization` 0.55-0.6 在 48GB 上約保留 26-29GB 給 ASR，剩約 19-22GB。
- IndexTTS2 推論峰值（6.3 推算 6-10GB，社群佐證 < 12GB）可容納且有餘裕。**共存於單張 48GB GPU 可行**。
- 效能風險：RTX 3060 上社群報 RTF 13.1（17.38 秒音檔耗約 228 秒），但該卡算力弱；RTX 6000 Ada 屬 Ada Lovelace（與 RTX 4090 同代），實際延遲會顯著更低。惟 IndexTTS2 的 GPT 尚無成熟 vLLM 加速（社群僅對 v1 有 vLLM 分支，v2 加速「仍在研究」），out-of-box 延遲可能高於 v1，須做效能 spike。〔社群〕 https://github.com/index-tts/index-tts/issues/585 、 https://github.com/Ksuriuri/index-tts-vllm

---

## 7. 推論與服務化整合（主問題 6）

### 7.1 官方提供的推論方式

1. Python API：`from indextts.infer_v2 import IndexTTS2` → `IndexTTS2(cfg_path=..., model_dir=...)` → `tts.infer(...)`，直接寫出 wav。〔官方 primary〕
2. WebUI（Gradio）：`uv run webui.py`，`http://127.0.0.1:7860`。〔官方 primary〕
3. CLI 腳本：`PYTHONPATH="$PYTHONPATH:." uv run indextts/infer_v2.py`。〔官方 primary〕
4. **官方無 HTTP／OpenAI 相容端點**，README 未提供任何 FastAPI 或 `/v1/audio/speech`。〔官方 primary〕 https://github.com/index-tts/index-tts/blob/main/README.md

### 7.2 社群服務化選項（標記社群）

1. `csllpr/index-tts-fastapi`：OpenAI 相容 `POST /v1/audio/speech`，接受 `model`／`input`／`voice`／`response_format`（`mp3`/`wav`/`ogg`）／`sample_rate`／`stream`／`speed`／`gain`；但為 v1 模型、**無情緒參數**。〔社群〕 https://github.com/csllpr/index-tts-fastapi
2. `seeingterra/index-tts-english-api-extended`：IndexTTS2 + OpenAI 相容 API（Windows／英文取向，Voxta-style 端點）。〔社群〕 https://github.com/seeingterra/index-tts-english-api-extended
3. `Ksuriuri/index-tts-vllm`：為 IndexTTS 加 vLLM 加速（v1 為主）。〔社群〕 https://github.com/Ksuriuri/index-tts-vllm

### 7.3 對齊 `/api/tts/speech` 契約的整合評估

1. 可行性：以 `IndexTTS2.infer()` 為核心、包一層薄 FastAPI，映射 `/api/tts/speech`（model/input/voice/response_format/stream）即可，整合成本低。可參考社群 FastAPI 實作，但需自行加上 IndexTTS2 情緒參數。
2. 取樣率：IndexTTS2 原生 vocoder 為 22.05kHz（BigVGAN v2 22khz）；消費端契約要求 24kHz/mono/16-bit。**adapter 必須做 22.05kHz → 24kHz 重採樣 + 轉 mono + 16-bit PCM**（`torchaudio`／`soundfile`／`librosa` 皆可），此為必要步驟而非可選，與原生即 24kHz 的模型不同。〔官方 primary，config.yaml〕 https://huggingface.co/IndexTeam/IndexTTS-2/raw/main/config.yaml
3. 契約缺口：OpenAI `/audio/speech` 形狀（model/input/voice/response_format/stream）**不含情緒欄位**；IndexTTS2 的 `emo_audio_prompt`／`emo_vector`／`emo_text`／`emo_alpha` 無處承載。若要暴露情緒控制，須擴充契約（例如加自訂欄位或以 `voice` 打包情緒設定）。此為整合設計決策點，非阻斷項。

---

## 8. 授權（主問題 7）

1. 授權名稱：**bilibili Model Use License Agreement**（非 Apache／MIT／OSI 開源，屬 source-available 的自訂授權）。
   - repo 內 `LICENSE`、`LICENSE_ZH.txt`（中英雙版）。〔官方 primary〕 https://github.com/index-tts/index-tts/blob/main/LICENSE
   - HF 權重 `LICENSE.txt`（10.6 kB）、`LICENSE_ZH.txt`。〔官方 primary〕 https://huggingface.co/IndexTeam/IndexTTS-2/raw/main/LICENSE.txt
   - `pyproject.toml` 宣告 `license = "LicenseRef-Bilibili-IndexTTS"`，package `indextts` 版本 `2.0.0`。〔官方 primary〕 https://raw.githubusercontent.com/index-tts/index-tts/main/pyproject.toml
   - **程式碼與權重同一套 bilibili 授權，無分別的寬鬆程式碼授權**（此與早期認知的 Apache 不同，以現行 repo 為準）。
2. 授權範圍：授予「worldwide, non-exclusive, non-transferable, royalty-free limited license」。〔官方 primary〕 https://huggingface.co/IndexTeam/IndexTTS-2/raw/main/LICENSE.txt
3. 自架與商用：
   - 商用門檻條款（逐字）：「If You intend to Use, or have already Used, the Model or any Derivative Work, and either (i) your or any of your Affiliates' products or services had more than 100 million monthly active users in the immediately preceding calendar month, or (ii) your or any of your Affiliates' annual revenue in the immediately preceding calendar year exceeded RMB 1 billion, You must request a separated license from us」。
   - 即：月活 > 1 億 或 年營收 > 人民幣 10 億，須另申請授權；未達門檻者在 royalty-free limited license 下可自架使用。授權文本**未逐句明文寫「商用允許」**，故對商業自架是否完全無虞，建議法務確認（見風險）。
4. 使用限制：明文禁止高風險用途（醫療診斷、自動駕駛、軍事、自動化決策等）；衍生用途限制（不得用以改進 bilibili 以外的商業 AI 系統）；使用者負第三方索賠之賠償責任。治理法為中國法、上海仲裁委員會管轄。〔官方 primary〕 https://github.com/index-tts/index-tts/blob/main/LICENSE
5. 對 Vibe-Vox 的意涵：作為未達門檻的自架平台，授權足以自架與（限制下）商用；但相對 Qwen3-TTS 的 Apache-2.0，這是更受限的授權，且治理法為中國法，屬需法務確認的變更點。

---

## 9. 專案活躍度與成熟度（次問題 8，社群為主）

1. 熱度與規模：Stars 22.1k、Forks 2.7k、246 commits。〔官方 primary，repo 頁〕 https://github.com/index-tts/index-tts
2. Release：頁面列出的最新 release 標為「IndexTTS-1.5」，日期 2025/09/01（README 另記 IndexTTS2 於 2025/09/08 釋出）。〔官方 primary〕 https://github.com/index-tts/index-tts
3. Issue 狀態：開放 issue 364 件，顯示活躍但也累積相當數量待處理問題。〔官方 primary〕
4. 已知問題（社群）：
   - 弱卡效能：RTX 3060 12GB 上 RTF 13.1，速度慢。〔社群〕 https://github.com/index-tts/index-tts/issues/585
   - 批次穩定性：長批次合成後出現 CUDA device-side assert。〔社群〕 https://github.com/index-tts/index-tts/issues/364
   - v2 尚無成熟官方 vLLM 加速路徑。〔社群〕 https://github.com/Ksuriuri/index-tts-vllm
5. 生產自架經驗：已有社群 FastAPI／OpenAI 相容封裝（見 7.2），顯示有人在服務化落地；但未見大規模生產環境長期穩定性的第一手權威報告。**此點無權威來源，需自行 spike 驗證。**

---

## 10. 落地前必辦清單（依結論導出）

1. 效能 spike：在 RTX 6000 Ada 上量測 IndexTTS2 FP16 的實際延遲／RTF 與峰值 VRAM，確認與 ASR 共存下的並發能力（對照 vLLM 0.55-0.6 的剩餘記憶體）。
2. 台灣口語化 spike：以台灣口音參考音 + 台灣口語文字，實測發音（含拼音覆寫）、用詞、語調可接受度。
3. adapter 工程：薄 FastAPI 包 `IndexTTS2.infer()` → `/api/tts/speech`；實作 22.05kHz→24kHz/mono/16-bit 重採樣；設計情緒參數如何在 OpenAI 形狀外承載（自訂欄位或 `voice` 打包）。
4. 法務確認：bilibili Model Use License Agreement 對本專案商業自架的適用性，及與現行 Apache 依賴的相容性。

---

## 附錄：本文引用的官方 primary 來源清單

- repo：https://github.com/index-tts/index-tts
- README：https://github.com/index-tts/index-tts/blob/main/README.md 、 https://raw.githubusercontent.com/index-tts/index-tts/main/README.md
- 論文 v1：https://arxiv.org/abs/2502.05512
- 論文 v2：https://arxiv.org/abs/2506.21619
- demo：https://index-tts.github.io/index-tts2.github.io/
- HF v2 模型卡與檔案：https://huggingface.co/IndexTeam/IndexTTS-2 、 https://huggingface.co/IndexTeam/IndexTTS-2/tree/main
- config.yaml：https://huggingface.co/IndexTeam/IndexTTS-2/raw/main/config.yaml
- LICENSE（repo）：https://github.com/index-tts/index-tts/blob/main/LICENSE
- LICENSE.txt（權重）：https://huggingface.co/IndexTeam/IndexTTS-2/raw/main/LICENSE.txt
- pyproject.toml：https://raw.githubusercontent.com/index-tts/index-tts/main/pyproject.toml

社群來源（已於文中標記）：issue #585、issue #364、Ksuriuri/index-tts-vllm、csllpr/index-tts-fastapi、seeingterra/index-tts-english-api-extended。
