# GLM-TTS 評估：是否滿足 Vibe-Qwen 需求

- 調查日期：2026-07-23
- 對象：`zai-org/GLM-TTS`（Zhipu AI / z.ai，GLM 團隊）
- 評估目的：作為 Vibe-Qwen TTS 供應端的第三候選，與 Qwen3-TTS、IndexTTS2 對比
- 來源標記：`官方 primary` 指官方 README／原始碼／arXiv 論文／模型卡／LICENSE；`社群` 指第三方轉述

---

## 一、結論摘要（先行）

### (A) 克隆音色 + 情緒控制（可逐句調）：部分（clone 是、逐句解耦情緒否）

1. Zero-shot voice cloning：支援。僅需 3-10 秒參考音，但**必須附上參考音逐字稿**（`prompt_text`）。
2. 情緒／風格逐句控制：**不支援**。GLM-TTS 的「情感」是**訓練期**的 RL 獎勵訊號（GRPO 的 Emotion reward），**推論期沒有任何情緒輸入欄位**（無 emotion 參數、無 instruction 參數、輸入 schema 只有 `prompt_text`／`prompt_speech`／`syn_text`）。
3. Timbre 與 emotion **未解耦**：合成語音的情緒／韻律來自參考音本身與 RL 學到的表現力，無法在固定克隆音色上獨立指定情緒，亦無法逐句切換情緒。這與 Qwen3-TTS clone 不支援情緒是**同一類限制**，與 IndexTTS2 的 timbre/emotion 解耦**相反**。
   - 來源：`官方 primary` https://github.com/zai-org/GLM-TTS/blob/main/examples/example_zh.jsonl ／ https://github.com/zai-org/GLM-TTS/blob/main/glmtts_inference.py ／ https://arxiv.org/abs/2512.14291

### (B) 台灣口語化中文：部分（有台灣國語佐證，但口語化語感需實測）

1. 官方明列語言：「主要支持中文，同時支持英文混合文本」（primarily Chinese, also English mixed text）。
2. 台灣相關的第一手依據：論文 Table 1 將 GLM-TTS 的 speech tokenizer 對「Taiwan Mandarin」測試集做 ASR 評測，CER 由 GLM4-Voice 的 49.09% 改善至 GLM-TTS 的 16.92%。此為**tokenizer 對台灣國語聲學的辨識/重建能力**佐證，非 TTS 端的「台灣口語化語感」保證。
3. 判定：以台灣口音參考音做 zero-shot，帶出台灣國語口音有第一手佐證支撐；但「台灣口語化」（用詞、語助詞、句法）取決於輸入文本與參考音，**官方無此語感的直接依據，需實測**。
   - 來源：`官方 primary` https://arxiv.org/abs/2512.14291（Table 1、Section 2.3）

### (C) 單張 48GB GPU + 現行 adapter：是（GPU 充裕、原生 24kHz 契合；但整合工要自建 HTTP 層）

1. VRAM：權重總計約 8.9 GB（FP32 落盤）。推論期 VRAM **推算**約 7-11 GB（視精度），與 VibeVoice-ASR（vLLM `gpu_memory_utilization` 0.55-0.6，約佔 26-29 GB）**可共存於單張 48GB**，餘量約 19-22 GB。無官方 VRAM 數字，此為依權重與參數量的推算。
2. 取樣率：**預設原生 24kHz**（專用 24kHz mel + HiFT vocoder），**完全命中消費端 24kHz/mono/16-bit 契約，無需重採樣**。`torchaudio.save` 輸出單聲道 wav（mono／預設 int16）。
3. adapter 整合：官方**只提供 CLI 與 Gradio，無 HTTP server、無 OpenAI 相容端點、無 vLLM**。包進 `TtsClient` 需自建一層 HTTP 服務暴露 `/api/tts/speech`；OpenAI 形狀**無須擴充情緒欄位**（GLM-TTS 本就無情緒輸入）；`voice` 需映射為一組（`prompt_speech` + `prompt_text`）。
   - 來源：`官方 primary` https://github.com/zai-org/GLM-TTS/blob/main/glmtts_inference.py ／ https://huggingface.co/zai-org/GLM-TTS/tree/main ／ https://github.com/zai-org/GLM-TTS/blob/main/utils/hift_util.py

### 總判：可並存新增（作為 Qwen3-TTS 的自架替代），不建議取代 IndexTTS2

1. 對「克隆音色 + 逐句情緒」這個**關鍵驅動需求**，GLM-TTS **不達標**（無推論期情緒控制、timbre/emotion 未解耦），與 Qwen3-TTS 同樣不解決情緒問題。唯一達標的仍是 IndexTTS2。因此**不建議以 GLM-TTS 取代 IndexTTS2** 的情緒克隆場景。
2. 但 GLM-TTS 相對 Qwen3-TTS 有三項自架優勢：**開源權重可自架**、**原生 24kHz（免重採樣）**、**授權寬鬆（code Apache-2.0／HF 權重卡 MIT，皆允許商用自架、無門檻無管轄條款）**。相對 IndexTTS2 的優勢是**授權更好（IndexTTS2 為 bilibili source-available）＋原生 24kHz（IndexTTS2 為 22.05kHz 需重採樣）**；相對 IndexTTS2 的劣勢是**無情緒解耦**。
3. 落地建議：**可並存新增**一條「高音質、中文為主、原生 24kHz、寬鬆授權、可自架」的一般 TTS 通道，由 GLM-TTS 承接**無逐句情緒需求**的合成；情緒克隆場景保留給 IndexTTS2。兩者皆非 vLLM，須各自獨立服務化。

---

## 二、逐題佐證

### 1. 版本譜系與定位

1. 定位：GLM-TTS 全名「Controllable & Emotion-Expressive Zero-shot TTS with Multi-Reward Reinforcement Learning」，Zhipu AI 產品級 TTS。
2. 架構：兩階段。text-to-token 自迴歸模型（`LlamaForCausalLM`，hidden 2048、28 層、GQA 16/4、vocab 98304、FP32）＋ token-to-waveform 的 flow-matching 擴散模型，末端接 vocoder。整體規模論文標示 **1.5B**（與 LLM 權重 6.2 GB／FP32 ≈ 1.55B 相符）。
3. 語音 tokenizer：Whisper-VQ，token rate 由 12.5Hz 提升至 25Hz、詞表由 16k 擴至 32k。
4. 與 GLM-4-Voice 的關係：**有關係**。論文 Table 1 以「GLM4-Voice」tokenizer 作為對照基線做 ASR 評測，GLM-TTS 為同團隊在 speech tokenizer 上的改良延續（非同一模型）。README／論文未宣稱直接繼承 GLM-4-Voice 的完整模型。
5. 確切 URL：
   - GitHub：https://github.com/zai-org/GLM-TTS `官方 primary`
   - 論文 arXiv：https://arxiv.org/abs/2512.14291（HTML：https://arxiv.org/html/2512.14291v1）`官方 primary`
   - HuggingFace 模型卡：https://huggingface.co/zai-org/GLM-TTS `官方 primary`
   - ModelScope：`ZhipuAI/GLM-TTS`（README 標示）`官方 primary`
   - 官方 demo/服務：https://audio.z.ai/ `官方 primary`
   - LLM 架構佐證：https://huggingface.co/zai-org/GLM-TTS/raw/main/llm/config.json `官方 primary`

### 2. Voice cloning

1. 支援 zero-shot voice cloning，免針對說話人微調。
2. 參考音長度：README 明列「3-10 秒」提示音。
3. 輸入需求：**參考音（`prompt_speech`）＋ 該參考音的逐字稿（`prompt_text`）＋ 待合成文本（`syn_text`）**。輸入 schema 由 `examples/example_zh.jsonl` 直接證實。這與只需參考音的系統不同，缺逐字稿會影響對齊品質。
4. 另有 LoRA-based voice customization（需訓練 LoRA，非 zero-shot），屬進階客製，非本次即用路徑。
   - 來源：`官方 primary` https://github.com/zai-org/GLM-TTS/blob/main/README.md ／ https://github.com/zai-org/GLM-TTS/blob/main/examples/example_zh.jsonl ／ https://github.com/zai-org/GLM-TTS/blob/main/configs/lora_adapter_configV3.1.json

### 3. 情緒／風格控制（關鍵）

1. 「情感表現力」的來源：**訓練期** GRPO 多獎勵 RL，四項核心 reward 為 CER、SIM、Emotion、Laughter；用以優化模型的表現力韻律。
2. **推論期無情緒控制介面**：
   - `glmtts_inference.py` 參數只有 `--data`／`--exp_name`／`--use_cache`／`--use_phoneme`／`--sample_rate`，**無 emotion／instruction 參數**。
   - 輸入 jsonl **無情緒欄位**。
   - 全庫程式碼中 `emotion` 只出現在 `grpo/`（RL 訓練）與 README／README_zh，`instruction` 僅 1 處。無推論期情緒標籤／情緒描述／獨立情緒參考音的輸入路徑。
3. timbre 與 emotion **未解耦**、**無法逐句調**：情緒隨參考音與模型學到的韻律走，無法在固定克隆音色上獨立指定或逐句切換情緒。標題中的「Controllable」指的是**發音控制**（phoneme + text 混合輸入）與取樣策略，非情緒控制。
   - 來源：`官方 primary` https://github.com/zai-org/GLM-TTS/blob/main/glmtts_inference.py ／ https://github.com/zai-org/GLM-TTS/blob/main/examples/example_zh.jsonl ／ https://arxiv.org/abs/2512.14291（GRPO 四獎勵、無推論期情緒輸入）

### 4. 語言支援

1. 官方明列：主要中文、支援中英混合文本。
2. 中文方言/口音：論文以 Sichuan、Jiao-Liao Mandarin、**Taiwan Mandarin**、Cantonese、Shanghai 等作為 tokenizer 的 ASR 測試集；Section 2.3 稱已納入大規模方言資料以強化方言理解。Taiwan Mandarin 測試集 CER 由 49.09%（GLM4-Voice）改善至 16.92%（GLM-TTS）。
3. 誠實區分：上述為 **tokenizer 對台灣國語聲學的辨識/重建**佐證；**「台灣口語化語感」（用詞、語助詞、句法）官方無直接依據，需以台灣口音參考音 + 台灣用語文本實測**。
   - 來源：`官方 primary` https://arxiv.org/abs/2512.14291（Table 1、Section 2.3）／ https://github.com/zai-org/GLM-TTS/blob/main/README.md

### 5. 模型大小與 VRAM

1. 權重檔（HuggingFace，FP32 落盤）實測大小：
   - `llm/model-00001/00002-of-00002.safetensors` 合計 ≈ 6.21 GB（AR LLM，約 1.55B 參數）
   - `speech_tokenizer/model.safetensors` ≈ 1.63 GB（Whisper-VQ，推論克隆時需載入以 token 化參考音）
   - `flow/flow.pt` ≈ 0.90 GB（flow-matching DiT）
   - `hift/hift.pt` ≈ 83 MB（24kHz HiFT vocoder）
   - `vocos2d/generator_jit.ckpt` ≈ 60 MB（roadmap 的 2D Vocos vocoder）
   - 總計 ≈ 8.9 GB
2. 精度：LLM `torch_dtype: float32`（落盤 FP32）；BF16 推論可將 LLM 壓至約 3.1 GB。
3. VRAM **推算**（無官方數字）：推論期權重 + 活化 + 短序列 KV cache，約 7-11 GB（視精度）。
4. 共存判定：與 VibeVoice-ASR（vLLM 佔約 26-29 GB）**可共存於單張 48GB**，餘量約 19-22 GB，充裕。
   - 來源：`官方 primary` https://huggingface.co/zai-org/GLM-TTS/tree/main ／ https://huggingface.co/zai-org/GLM-TTS/raw/main/llm/config.json ／ https://arxiv.org/abs/2512.14291（1.5B）

### 6. 推論與服務化

1. 官方推論方式：Python CLI（`python glmtts_inference.py --data=example_zh`）、shell（`bash glmtts_inference.sh`）、Gradio（`python -m tools.gradio_app`）。支援串流（streaming inference）。**無 HTTP server、無 OpenAI 相容端點、無 vLLM 整合**。
2. 取樣率（重點）：
   - **預設原生 24kHz**：`--sample_rate` 預設 24000；`hift_util.py` 的 HiFT vocoder `self.sample_rate = 24000`、`n_fft=1920`、`hop_size=480`，並有對應 24kHz mel（`load_frontends` 中 24000 分支 `hop_size=480, n_fft=1920`）。**直接符合 24kHz/mono/16-bit，無需重採樣**。
   - 32kHz 屬 Vocos2D vocoder（`vocos_util.py` `FS32K = 32000`），對應論文 Section 2.7「To support 32 kHz high-quality wideband speech synthesis」，屬 README roadmap「2D Vocos vocoder update in progress」。若改走 32kHz 分支則需下採樣回 24kHz。
3. adapter 整合成本：可包在 `TtsClient` 後產出 wav（`torchaudio.save` 輸出單聲道 wav）。需自建 HTTP 服務暴露 `/api/tts/speech`；OpenAI 形狀無須加情緒欄位；`voice` 對應到（`prompt_speech` + `prompt_text`）配對，需在服務端維護音色字典（參 `configs/spk_prompt_dict.yaml` 之預設音色 token 表）。
   - 來源：`官方 primary` https://github.com/zai-org/GLM-TTS/blob/main/glmtts_inference.py ／ https://github.com/zai-org/GLM-TTS/blob/main/utils/hift_util.py ／ https://github.com/zai-org/GLM-TTS/blob/main/utils/vocos_util.py ／ https://github.com/zai-org/GLM-TTS/blob/main/README.md

### 7. 授權（關鍵對比點）

1. 程式碼（GitHub）：**Apache-2.0**（LICENSE 檔為標準 Apache-2.0，版權 Zhipu AI 2025；GitHub API SPDX 亦回報 `Apache-2.0`）。
2. 權重（HuggingFace 模型卡 frontmatter）：**`license: mit`**。
3. **來源間不一致**：GitHub code 標 Apache-2.0，HF 權重卡標 MIT。兩者皆為寬鬆授權，**均允許自架與商用、無商用門檻、無管轄地限制、無使用量閘門**。落地前建議向官方確認權重的最終授權字樣以消歧。
4. 對比：相對 IndexTTS2（bilibili source-available，有較嚴條款）為**明顯優勢**；相對 Qwen3-TTS（Apache-2.0）為**同級或更寬**，且 GLM-TTS 權重可自架，優於 Qwen3-TTS 強版本的 API/封閉取用。
   - 來源：`官方 primary` https://github.com/zai-org/GLM-TTS/blob/main/LICENSE ／ https://huggingface.co/zai-org/GLM-TTS （模型卡 `license: mit`）

### 8. 活躍度與成熟度（次問題）

1. GitHub 指標（2026-07-23 查詢，GitHub API）：Stars 1044、Forks 131、Open issues 46。
2. 建立於 2025-12-06，最後 push 2026-04-10（距今約 3 個月）。近期 commit 多為 Ascend NPU 支援。
3. **無 GitHub Releases**（僅以 git 追蹤，無版本 tag）。README roadmap 標示 `GLM-TTS_RL`（RL 優化權重）與 2D Vocos vocoder「coming soon / in progress」，即最強版本權重尚未釋出。
4. 生產自架踩雷：無第一手可證資料（HF 顯示「1 file scanned as suspicious」為平台掃描標註，非確認問題）；生產經驗**無來源，需實測**。
   - 來源：`官方 primary` https://github.com/zai-org/GLM-TTS （GitHub API 指標與 commit 歷史）／ https://github.com/zai-org/GLM-TTS/blob/main/README.md（roadmap）

---

## 三、與 Qwen3-TTS／IndexTTS2 對照表

| 面向 | GLM-TTS | Qwen3-TTS | IndexTTS2 |
|---|---|---|---|
| Zero-shot clone | 是（需參考音 + 逐字稿） | 是 | 是 |
| 逐句情緒 + timbre/emotion 解耦 | **否** | 否 | **是** |
| 原生取樣率 | **24kHz（免重採樣）** | 依 API | 22.05kHz（需重採樣） |
| 授權（自架/商用） | code Apache-2.0 / 權重卡 MIT（寬鬆） | Apache-2.0（但強版本多為 API） | bilibili source-available（較嚴） |
| 開源權重可自架 | 是（約 8.9 GB） | 受限 | 是 |
| vLLM | 無（AR 為 Llama 架構，理論可自接，需自寫膠水；**推算**） | 無 | 無成熟 |
| 官方服務化 | 僅 CLI/Gradio，需自建 HTTP | API | 需自建 |

> 表中「逐句情緒 + 解耦」為本專案關鍵需求，僅 IndexTTS2 達標。

---

## 四、待實測項目（無第一手依據）

1. 台灣口語化語感（用詞/語助詞/句法）與台灣口音 zero-shot 的實際自然度。
2. 推論期實際 VRAM 佔用（官方未給數字，本文為推算）。
3. 與 VibeVoice-ASR 同卡並行時的實際峰值記憶體與延遲。
4. HF 權重的最終授權字樣（code Apache-2.0 vs 權重卡 MIT 不一致，需向官方確認）。
5. 生產自架穩定性與已知踩雷（無第一手資料）。
