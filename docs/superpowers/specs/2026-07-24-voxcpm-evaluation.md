# 研究：VoxCPM（OpenBMB）是否滿足 Vibe-Qwen 的合規 + clone + 逐句情緒 + 台灣口語化需求

日期：2026-07-24
研究問題：開源 TTS 專案 VoxCPM（GitHub `OpenBMB/VoxCPM`）能否滿足 Vibe-Qwen 的需求，第一級為**授權合規**（能否公司內部自架、商用），其次為 (A) 對「克隆出的音色」做情緒控制且最好可逐句調整、(B) 產出台灣口語化中文語音、(C) 與 VibeVoice-ASR 共存於單張 RTX 6000 Ada 48GB 並包進現行 `TtsClient` adapter 對齊 `/api/tts/speech` 契約。
可信度分級：每條結論後標註來源，區分「官方 primary」與「社群」。無來源佐證者明寫「無來源，無法證實／需實測」，不臆測。
查證方法註記：本文對合規關鍵事實（VoxCPM2 存在性、授權標籤）以 Playwright 直接讀取 HuggingFace 真實 DOM，並以 GitHub API 取回權威 repo 統計，避免二手轉述或摘要幻覺；技術規格以 arXiv 技術報告與官方 README／模型卡佐證。

---

## 1. 結論摘要（結論先行）

**第一級：授權合規 —— 合規，可商用自架。**
- code 與 weights 皆 Apache-2.0。VoxCPM2 模型卡真實 DOM 逐字為「Fully Open-Source & Commercial-Ready — Apache-2.0 license, free for commercial use」，授權標籤 `License: apache-2.0`。〔官方 primary〕 https://huggingface.co/openbmb/VoxCPM2
- repo 內 `LICENSE` 為標準 Apache-2.0 全文（Copyright OpenBMB），無自訂加註、無研究限定、無月活／營收門檻、無管轄地條款。〔官方 primary〕 https://raw.githubusercontent.com/OpenBMB/VoxCPM/main/LICENSE ；GitHub API 回報 `spdx_id: Apache-2.0`。
- 唯一限制為「倫理使用聲明」而非授權限制：禁止用於假冒／詐騙／不實資訊、AI 生成內容應標示。對「公司內部自架、商用陪練工具」不構成障礙。〔官方 primary〕 https://huggingface.co/openbmb/VoxCPM2/raw/main/README.md
- 相對定位：與 Qwen3-TTS（Apache-2.0）、GLM（Apache/MIT）同級的乾淨開源授權，**優於 IndexTTS2 的 bilibili source-available 授權**（後者非 OSI 開源、需法務確認）。

**(A) clone + 情緒（可逐句調）：可行（有一項取捨需知）。**
- VoxCPM2 官方明列三種模式，其中 Controllable Voice Cloning 支援「僅需參考音的 zero-shot 克隆」**同時**接受行內自然語言風格指令（emotion／pace／style），且明述「preserving timbre」＝音色與情緒解耦、可並用。〔官方 primary〕 https://huggingface.co/openbmb/VoxCPM2
- 風格指令以 `(...)` 括號前綴於文字，隨每次 `model.generate()` 呼叫獨立生效，逐句切分後每句一次呼叫即可各自指定情緒＝逐句可調。〔官方 primary〕 https://voxcpm.readthedocs.io/en/latest/usage_guide.html
- **取捨**：另一種 Hi-Fi／Ultimate Cloning 模式（參考音＋逐字稿，音色相似度最高）會**忽略風格指令**。即「最高音色保真」與「風格控制」二擇一；要逐句情緒就走 Controllable 模式（音色仍保留、相似度略低於 Hi-Fi）。〔官方 primary〕 https://voxcpm.readthedocs.io/en/latest/usage_guide.html
- 若 (A) 成立，VoxCPM 是 IndexTTS2 停案後**目前唯一達標**的候選（Qwen3-TTS clone 無情緒、GLM 情緒推論吃不進，均已淘汰）。

**(B) 台灣口語化：部分／需實測（無官方來源）。**
- 官方明列 30 種語言含 Chinese，並含 9 種**中國大陸**方言（四川话, 粤语, 吴语, 东北话, 河南话, 陕西话, 山东话, 天津话, 闽南话），**未列台灣國語／台灣口音**。〔官方 primary〕 https://arxiv.org/abs/2606.06928
- 機制上 zero-shot 克隆會帶入「accent, emotional tone, rhythm, pacing」，故以台灣參考音帶出台灣口音有技術路徑，但「台灣口語化語感」官方無任何依據。**無來源，需實測 spike。**

**(C) 單 48GB GPU 共存 + 現行 adapter：可行。**
- VoxCPM2 推論約 ~8 GB VRAM（bf16）。vLLM `gpu_memory_utilization` 壓 0.55-0.6 時保留約 26-29GB 給 ASR，剩約 19-22GB，容納 8GB TTS 進程有餘裕。〔官方 primary（VRAM）〕 https://huggingface.co/openbmb/VoxCPM2
- VoxCPM 以「獨立原生 Python 進程」推論（不經 vLLM），與 Vibe-Qwen 既定「TTS 走獨立原生進程 + `TtsClient` adapter」架構一致。原生輸出 48kHz，adapter 內需重採樣為 24kHz/mono/16-bit（clean downsample，見 §7）。

**總判：應採用（作為情緒可控 clone 引擎），並列為 IndexTTS2 停案後首選。** 授權比 IndexTTS2 乾淨（Apache-2.0 vs bilibili），能力上以「音色保留 + 逐句風格控制」補上 Qwen3-TTS／GLM 的情緒缺口。落地前保留兩道 spike：台灣口語化（無官方依據）、Controllable 模式音色相似度是否足夠（因逐句情緒需求會排除 Hi-Fi 模式）。

---

## 2. 版本譜系與權威來源（授權主問題 1）

使用者問「VoxCPM 2 是否合規」——**VoxCPM 2 確實存在**，且為目前主線版本（GitHub repo 標題已更名為 VoxCPM2）。三個版本同屬官方 repo `OpenBMB/VoxCPM`。

| 版本 | 官方釋出 | 參數／原生取樣率 | 語言 | 論文 |
| --- | --- | --- | --- | --- |
| VoxCPM-0.5B（原始） | 2025.09 | 0.5B / 16kHz | zh, en（雙語） | arXiv:2509.24650 |
| VoxCPM1.5 | 2025.12 | 基於 MiniCPM4-0.5B / 44.1kHz | zh, en | 沿用 2509.24650 |
| VoxCPM2 | 2026.04 | 2B / 48kHz（AudioVAE 16kHz 編碼、48kHz 重建） | 30 語言 + 9 中國方言 | arXiv:2606.06928 |

版本、釋出時序來源。〔官方 primary〕 https://github.com/OpenBMB/VoxCPM ；repo 建立於 2025-09-16，最近 push 2026-07-08（GitHub API）。

確切 URL：
1. 官方 repo：https://github.com/OpenBMB/VoxCPM 〔官方 primary〕
2. VoxCPM 原始論文：https://arxiv.org/abs/2509.24650 （2025-09-29 送件）〔官方 primary〕
3. VoxCPM2 技術報告：https://arxiv.org/abs/2606.06928 （2026-06-05 送件）〔官方 primary〕
4. 官方文件（readthedocs）：https://voxcpm.readthedocs.io/en/latest/usage_guide.html （中文版 `/zh-cn/`）〔官方 primary〕
5. HF 模型卡：
   - VoxCPM2：https://huggingface.co/openbmb/VoxCPM2 〔官方 primary〕
   - VoxCPM1.5：https://huggingface.co/openbmb/VoxCPM1.5 〔官方 primary〕
   - VoxCPM-0.5B：https://huggingface.co/openbmb/VoxCPM-0.5B 〔官方 primary〕
6. Demo Space：https://huggingface.co/spaces/OpenBMB/VoxCPM-Demo 〔官方 primary〕

本評估以 **VoxCPM2** 為對象（情緒／風格控制與多語言能力僅 v2 完整具備；v0.5B 官方自述「limited direct control over emotion or speaking style」，見 §4）。

---

## 3. 授權與合規（授權主問題 2，第一級）

### 3.1 code 與 weights 授權
- **code**：`LICENSE` 檔為標準 Apache License 2.0 全文，僅填入 Copyright OpenBMB，無任何附加條款。〔官方 primary〕 https://raw.githubusercontent.com/OpenBMB/VoxCPM/main/LICENSE
- **weights（VoxCPM2）**：HF 模型卡 YAML frontmatter `license: apache-2.0`；頁面授權標籤連向 https://www.apache.org/licenses/LICENSE-2.0 。真實 DOM 逐字：「📜 Fully Open-Source & Commercial-Ready — Apache-2.0 license, free for commercial use」。〔官方 primary〕 https://huggingface.co/openbmb/VoxCPM2
- **weights（VoxCPM-0.5B / 1.5）**：亦為 `license: apache-2.0`。〔官方 primary〕 https://huggingface.co/openbmb/VoxCPM-0.5B/raw/main/README.md

### 3.2 是否允許自架 + 商用
**允許。** Apache-2.0 明確授予商用、修改、再散布權利，無 copyleft、無月活／營收門檻、無管轄地條款、無研究限定、無 OpenBMB 特有的附加使用政策綁在授權上。GitHub API 亦回報 `license.spdx_id = Apache-2.0`（標準識別）。〔官方 primary〕

### 3.3 需注意的非授權性限制
- **倫理使用聲明（非授權限制）**：VoxCPM2 模型卡載「Strictly forbidden to use for impersonation, fraud, or disinformation. AI-generated content should be clearly labeled.」屬 acceptable-use 倫理宣示，不改變 Apache-2.0 的法律授予；對內部商用陪練工具無實質阻礙（不涉假冒他人）。〔官方 primary〕 https://huggingface.co/openbmb/VoxCPM2/raw/main/README.md
- **版本差異需留意**：VoxCPM-0.5B 與 1.5 的模型卡另有一句**非約束性建議**「This model is released for research and development purposes only. We do not recommend its use in production or commercial applications without rigorous testing and safety evaluations.」這是**免責／建議語**而非授權限制（法律授權仍是 Apache-2.0）。**VoxCPM2 已移除此句**，改為明確背書「free for commercial use」。〔官方 primary〕 VoxCPM-0.5B：https://huggingface.co/openbmb/VoxCPM-0.5B/raw/main/README.md ；VoxCPM2 真實 DOM 未出現該句（Playwright 檢索 `research and development` 命中 NONE）。
- 合規建議：採用 **VoxCPM2**，授權立場最乾淨；若因故退回 0.5B/1.5，法律上仍可商用（Apache-2.0），但官方僅「建議先充分測試」，宜以自家測試紀錄佐證盡職。

### 3.4 相對競品的合規對比
| 專案 | 授權 | 是否 OSI 開源 | 商用自架 |
| --- | --- | --- | --- |
| VoxCPM / VoxCPM2 | Apache-2.0 | 是 | 允許 |
| Qwen3-TTS | Apache-2.0 | 是 | 允許（能力已淘汰） |
| GLM-TTS | Apache / MIT | 是 | 允許（能力已淘汰） |
| IndexTTS2 | bilibili Model Use License Agreement | 否（source-available） | 需法務確認（已停案） |

VoxCPM2 是本輪候選中**授權最無爭議**者之一。〔對比依據見各 sibling note 與上列官方來源〕

---

## 4. 情緒／風格控制與音色解耦（能力主問題 4，關鍵 go/no-go）

**支援，且與 clone 可並用、可逐句。** 機制為「行內自然語言風格描述」，非情緒向量或固定標籤。

- 官方三模式（VoxCPM2 模型卡逐字）：〔官方 primary〕 https://huggingface.co/openbmb/VoxCPM2
  - **Controllable Voice Cloning**：「Clone any voice from a short clip, with optional style guidance to steer emotion, pace, and expression **while preserving timbre**」——即音色（來自參考音）與情緒／風格（來自文字描述）**解耦並同時作用**。
  - **Ultimate（Hi-Fi）Cloning**：參考音 + 逐字稿的音檔續寫式克隆，最高保真。
  - **Voice Design**：僅憑自然語言描述生成全新語者，無參考音。
- 風格指令語法（官方文件範例，逐字保留）：〔官方 primary〕 https://voxcpm.readthedocs.io/en/latest/usage_guide.html
  ```python
  wav = model.generate(
      text="(slightly faster, cheerful tone)This is a cloned voice.",
      reference_wav_path="speaker.wav",
      cfg_value=2.0,
      inference_timesteps=10,
  )
  ```
  括號內 `(slightly faster, cheerful tone)` 前綴於目標文字，與 `reference_wav_path` 併用。
- **逐句可調**：風格隨每次 `generate()` 呼叫獨立生效；逐句切分後每句一次呼叫，即可各句指定不同情緒。官方文件明示風格為 per-generation。〔官方 primary〕 https://voxcpm.readthedocs.io/en/latest/usage_guide.html
- **關鍵取捨（務必知）**：官方文件明述「When Hi-Fi mode is enabled, the control instruction is **ignored**」。即最高保真克隆模式不吃風格指令。要逐句情緒，須使用 Controllable 模式（僅參考音、不帶逐字稿），此模式音色仍「preserving timbre」但相似度理論上略低於 Hi-Fi。〔官方 primary〕 https://voxcpm.readthedocs.io/en/latest/usage_guide.html
- 技術報告佐證能力存在：VoxCPM2 於單一 backbone 內統一「style-controllable voice cloning」與「high-fidelity continuation cloning」。〔官方 primary〕 https://arxiv.org/abs/2606.06928

> 對比 Qwen3-TTS：後者 clone 路徑不支援 instruct／情緒（已證實淘汰）。VoxCPM2 在 Controllable 模式下以「音色保留 + 逐句文字風格」直接補上此缺口，能力嚴格優於 Qwen3-TTS clone 路徑。與 IndexTTS2 相比，IndexTTS2 以獨立情緒參考音／情緒向量／情緒文字三路驅動、解耦更徹底；VoxCPM2 的情緒驅動僅「文字描述」一路，且與最高保真模式互斥——此為兩者差異，需以實測比對表達力是否足夠。

---

## 5. Voice cloning（能力主問題 3）

- **zero-shot、免微調**：僅需一段參考音即可克隆（Controllable 模式無需逐字稿）；Hi-Fi 模式另需參考音的逐字稿。〔官方 primary〕 https://voxcpm.readthedocs.io/en/latest/usage_guide.html
- **輸入**：
  - Controllable：`reference_wav_path`（僅參考音）＋行內風格文字。
  - Hi-Fi：`prompt_wav_path` ＋ `prompt_text`（逐字稿，官方建議以 ASR 取得而非手打）。
- **參考音規格**（官方 usage guide）：實務長度 5–30 秒；格式 WAV／FLAC／MP3（torchaudio 支援）；音檔越乾淨、音色保真越好；另有 `denoise` 參數可對參考音去噪。〔官方 primary〕 https://voxcpm.readthedocs.io/en/latest/usage_guide.html
- 克隆捕捉範圍（原始模型卡）：「timbre... accent, emotional tone, rhythm, and pacing」。〔官方 primary〕 https://huggingface.co/openbmb/VoxCPM-0.5B/raw/main/README.md

---

## 6. 語言支援與台灣口語化（能力主問題 5）

- **官方明列語言**：VoxCPM2 支援 30 語言（免語言標籤）：Arabic, Burmese, Chinese, Danish, Dutch, English, Finnish, French, German, Greek, Hebrew, Hindi, Indonesian, Italian, Japanese, Khmer, Korean, Lao, Malay, Norwegian, Polish, Portuguese, Russian, Spanish, Swahili, Swedish, Tagalog, Thai, Turkish, Vietnamese。〔官方 primary〕 https://huggingface.co/openbmb/VoxCPM2
- **中文方言**：另支援 9 種**中國大陸**方言：四川话, 粤语, 吴语, 东北话, 河南话, 陕西话, 山东话, 天津话, 闽南话。〔官方 primary〕 https://arxiv.org/abs/2606.06928
- **台灣口語化**：官方**未列**台灣國語／台灣口音；9 方言中的「闽南话」與台語相關但不等同「台灣華語口語」。技術報告與模型卡均無台灣相關宣稱。
  - 機制路徑存在（zero-shot 克隆帶入 accent／rhythm／pacing），故以台灣參考音帶出台灣口音有可行性，但**無官方依據，需實測 spike**。誠實區分：「官方明列語言支援」有中文；「台灣口語化語感」無來源，無法證實。
- 對比：VoxCPM-0.5B 原始版官方僅宣稱 zh／en 雙語且「其他語言不保證」；VoxCPM2 才擴至 30 語言。〔官方 primary〕 https://huggingface.co/openbmb/VoxCPM-0.5B/raw/main/README.md

---

## 7. 部署可行性（部署問題 6、7）

### 7.1 模型大小 / VRAM / 精度 / 共存
- **VoxCPM2**：2B 參數；VRAM 約 ~8 GB；精度 bf16（BF16）。〔官方 primary（參數／VRAM）〕 https://huggingface.co/openbmb/VoxCPM2 ；〔社群獨立佐證 VRAM ~8GB、48kHz、30 語言〕 https://toknow.ai/posts/voxcpm2-tokenizer-free-tts-30-languages-voice-design/
  - 註：bf16 精度來自模型卡摘要，未於真實 DOM 逐字複驗，可信度中；VRAM ~8GB 有官方 + 社群雙重佐證。
- **單 48GB 共存推算**：ASR（vLLM，`gpu_memory_utilization` 0.55-0.6）約占 26-29GB，剩約 19-22GB；VoxCPM2 原生進程 ~8GB 可容納且有餘裕。**共存無記憶體衝突**（推算，VRAM 為官方數字）。
- 效能參考：官方稱 RTF 約 ~0.3（RTX 4090），以 Nano-vLLM 加速可達 ~0.13。〔官方 primary〕 https://huggingface.co/openbmb/VoxCPM2 （RTX 6000 Ada 算力與 4090 同級，可作近似）

### 7.2 推論與服務化
- **Python API**（官方）：〔官方 primary〕 https://voxcpm.readthedocs.io/en/latest/usage_guide.html
  ```python
  from voxcpm import VoxCPM
  model = VoxCPM.from_pretrained("openbmb/VoxCPM2", load_denoiser=False)
  wav = model.generate(text="...", reference_wav_path="speaker.wav", cfg_value=2.0, inference_timesteps=10)
  # 串流：model.generate_streaming(text="...")
  ```
  `generate()` 主要參數：`text`（必填）、`reference_wav_path`、`prompt_wav_path`、`prompt_text`、`cfg_value`（預設 2.0，範圍 1.0–3.0）、`inference_timesteps`（預設 10，範圍 4–30）、`normalize`、`denoise`、`retry_badcase`。
- **CLI**：`voxcpm design` / `voxcpm clone` / `voxcpm batch`。〔官方 primary，README 摘要，可信度中〕
- **HTTP / OpenAI 相容**：官方稱可經 vLLM-Omni 提供 drop-in OpenAI 相容端點 `/v1/audio/speech`；另有 Nano-vLLM（高吞吐）、llama.cpp-omni（CPU/Metal/CUDA）。〔官方 primary，README 摘要，可信度中——建議實作前實測端點形狀〕 https://github.com/OpenBMB/VoxCPM
- **原生取樣率與重採樣**：VoxCPM2 原生 48kHz（AudioVAE 16kHz 編碼、48kHz 重建）。消費端契約要 24kHz/mono/16-bit，故 adapter 需 **48kHz→24kHz 降採樣 + 轉單聲道 + 16-bit PCM**（降採樣品質乾淨，優於 0.5B 的 16kHz 需升採樣）。〔官方 primary〕 https://arxiv.org/abs/2606.06928
- **adapter 整合**：`model.generate()` 直接產 wav，包一層薄 FastAPI 對齊 `/api/tts/speech` 即可，或直接用官方 vLLM-Omni OpenAI 端點。
- **OpenAI 形狀是否需擴充情緒欄位**：不必然。逐句風格以 `(cheerful tone)` 行內語法直接嵌入 `input` 文字即可傳遞，無需新增欄位；`voice` 欄位需對映到「參考音／語者」——但 OpenAI 相容端點如何傳參考音（reference_wav）官方文件未明述，**需實測端點契約**再決定是否擴充（例如以 voice=已註冊語者 ID 或另帶參考音路徑）。

---

## 8. 活躍度與成熟度（次問題 8，可社群來源）

- **GitHub**（GitHub API，權威）：stars 34,627、forks 3,968、open issues 100、最近 push 2026-07-08、建立於 2025-09-16。〔官方 primary（API）〕 https://api.github.com/repos/OpenBMB/VoxCPM
- HF VoxCPM2 模型卡 likes 1.51k；OpenBMB 組織 followers 4.38k。〔官方 primary（真實 DOM）〕 https://huggingface.co/openbmb/VoxCPM2
- 釋出節奏活躍：0.5B（2025.09）→ 1.5（2025.12，曾登 GitHub Trending 第一）→ 2（2026.04），約每季一版。〔官方 primary〕 https://github.com/OpenBMB/VoxCPM
- 第三方評測討論（社群，僅供參考、需自行複驗）：有文章稱 VoxCPM2「在相似度上勝過 ElevenLabs、但整體 benchmark 另有取捨」。〔社群〕 https://medium.com/@tentenco/voxcpm2-the-open-source-voice-model-that-beats-elevenlabs-on-similarity-but-the-full-benchmark-ffe408b50b87
- **生產自架經驗**：未找到第一手可信來源。**無來源，需自行 spike 驗證**（延遲、逐句串接、Controllable 模式音色相似度）。

---

## 9. 待驗缺口（落地前）

1. **台灣口語化**（(B)）：以台灣參考音在 Controllable 模式下 zero-shot，實測台灣華語口音／用詞是否自然。無官方依據。
2. **音色相似度取捨**（(A)）：逐句情緒需求排除 Hi-Fi 模式，須實測 Controllable 模式的音色相似度是否達陪練需求；若不足，評估「Hi-Fi 建立基礎音色 + Controllable 逐句情緒」能否折衷。
3. **OpenAI 相容端點契約**：vLLM-Omni `/v1/audio/speech` 如何傳參考音與行內風格，實測後決定 `TtsClient` adapter 與 `voice` 欄位對映，及是否需擴充情緒欄位。
4. **效能／延遲**：RTX 6000 Ada 上 Controllable 模式逐句呼叫的實際 RTF 與首包延遲；是否需 Nano-vLLM 加速。
5. **精度佐證**：bf16 為模型卡摘要所得，未逐字複驗，載入時以實際 dtype 為準。

---

## 附錄：來源清單

官方 primary：
- https://github.com/OpenBMB/VoxCPM
- https://api.github.com/repos/OpenBMB/VoxCPM （repo 統計）
- https://raw.githubusercontent.com/OpenBMB/VoxCPM/main/LICENSE
- https://huggingface.co/openbmb/VoxCPM2 （真實 DOM 複驗授權）
- https://huggingface.co/openbmb/VoxCPM2/raw/main/README.md
- https://huggingface.co/openbmb/VoxCPM1.5/raw/main/README.md
- https://huggingface.co/openbmb/VoxCPM-0.5B/raw/main/README.md
- https://voxcpm.readthedocs.io/en/latest/usage_guide.html （中文版 /zh-cn/）
- https://arxiv.org/abs/2606.06928 （VoxCPM2 技術報告）
- https://arxiv.org/abs/2509.24650 （VoxCPM 原始論文）

社群：
- https://toknow.ai/posts/voxcpm2-tokenizer-free-tts-30-languages-voice-design/
- https://medium.com/@tentenco/voxcpm2-the-open-source-voice-model-that-beats-elevenlabs-on-similarity-but-the-full-benchmark-ffe408b50b87
