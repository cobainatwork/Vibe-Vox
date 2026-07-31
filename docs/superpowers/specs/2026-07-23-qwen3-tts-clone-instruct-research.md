# 研究：Qwen3-TTS voice clone 是否支援 instruct／情緒控制

日期：2026-07-23
研究問題：在 Qwen3-TTS 的 voice clone（聲音克隆）音色上，是否能套用 instruct／情緒／風格控制？若官方不支援，社群鏈式做法是否為可行替代？
可信度分級：本文每條結論後標註來源，並區分「官方 primary source」與「社群／非官方」。無來源佐證者明寫「未找到來源，無法證實」。

---

## 1. 結論摘要（結論先行）

1. clone + instruct 直接可行性：**不可行（官方能力表與 API 簽章一致顯示不支援）**。
   - 開源權重 `qwen-tts` 套件的 `generate_voice_clone` 函式簽章不含 `instruct`／`emotion`／`style` 任何情緒控制欄位；官方模型能力表將 clone 模型（`*-Base`）的「Instruction Control」標為無。
   - 阿里雲 DashScope 託管的 voice clone 合成呼叫僅接受 `voice`（已註冊音色 id），同一次呼叫亦無情緒／instruct 欄位。
   - 判定依據為「能力表 + 函式簽章 + 託管 API 參數」三方一致，非官方單句明文宣告；官方文件未見一句「clone 不能使用 instruct」的直述句，此點以推得標示（見 3.3）。

2. 情緒控制僅存在於另外兩條路徑：`generate_custom_voice`（preset speaker + `instruct`）與 `generate_voice_design`（文字描述生成 + `instruct`）。此二者官方能力表明列「Instruction Control ✅」。因此「clone 與 preset／design 在 instruct 支援上有差異」的假設屬實（primary source 佐證）。

3. 鏈式做法（先用可帶 instruct 的路徑產出帶情緒音檔，再作為 clone 參考音）：**社群層級可行，但有硬性限制**。
   - clone 會忠實轉移參考音的韻律與情緒（社群來源佐證），故「design／custom+instruct 產生目標情緒音檔 → 當作 ref_audio 餵給 clone」可讓克隆音色帶上該情緒。
   - 限制邊界：情緒被「固定」在參考音所承載的單一語氣，**無法逐句即時調整**；要換情緒必須換一段不同情緒的參考音重跑。此為社群來源觀察，非官方保證。

4. 官方對此需求的態度：官方 GitHub 有兩則社群 feature request（Discussion #218、#253）要求 clone 的情緒／韻律控制與雙參考（音色 + 韻律）合成，**截至擷取時無 Qwen 團隊回覆、無 roadmap 承諾**。屬未定案。

---

## 2. 釐清：本專案指的引擎與各家模型關係（主問題 1）

### 2.1 本專案引擎的確切形態

專案 spec 所指的三條原生路徑（`generate_custom_voice`／`generate_voice_clone`／`generate_voice_design`）對應的是 **Qwen 團隊開源的 Qwen3-TTS 權重系列**，透過官方 `qwen-tts` PyPI 套件呼叫，非阿里雲託管 API。

- 官方原始碼庫：`QwenLM/Qwen3-TTS`（Apache-2.0 開源系列）。〔官方 primary source〕 https://github.com/QwenLM/Qwen3-TTS
- 官方 PyPI 套件 `qwen-tts`。〔官方 primary source〕 https://pypi.org/project/qwen-tts/
- 官方模型權重（Hugging Face）：`Qwen/Qwen3-TTS-12Hz-1.7B-Base`（clone）、`Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice`（preset+instruct）、`Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign`（文字描述生成），另有 0.6B 對應版本與 `Qwen/Qwen3-TTS-Tokenizer-12Hz`。〔官方 primary source〕 https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base 、 https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice

### 2.2 與其他 Qwen／阿里雲模型的區別

1. Qwen3-TTS（開源權重，本專案引擎） vs Qwen-TTS／qwen3-tts-flash（阿里雲 DashScope 託管 API）：兩者名稱相近但形態不同。DashScope 上另有 `qwen3-tts-flash`、`qwen-tts` 等託管模型 id，採 token 計費，屬服務化端點；本專案走的是可自架的開源權重。〔官方 primary source，DashScope 模型清單〕 https://www.alibabacloud.com/help/en/model-studio/models
2. Qwen2-Audio／Qwen-Audio：是「音訊理解（audio-in、文字或語音回應）」的大型音訊語言模型，**非 TTS 生成模型**，與本專案 TTS 供應端無關。〔官方 primary source〕 https://github.com/QwenLM/Qwen2-Audio 、 https://qwenlm.github.io/blog/qwen2-audio/
3. CosyVoice：阿里雲另一條獨立發展的 TTS 產品線（DashScope 上為 `cosyvoice-v*` 系列），與 Qwen3-TTS 為不同團隊、不同架構的平行路線，非同一模型。〔官方 primary source，Model Studio 語音合成模型清單〕 https://www.alibabacloud.com/help/en/model-studio/tts-model/ ；架構差異之敘述屬〔社群／非官方〕 https://pandaily.com/alibaba-open-sources-qwen3-tts-model-suite-delivering-multilingual-ultra-low-latency-speech-generation

---

## 3. voice clone 路徑實際接受的參數（主問題 2、3）

### 3.1 開源 `qwen-tts` 的 `generate_voice_clone` 完整參數

官方 README／模型卡的 clone 範例與參數如下（逐字保留原文）：

```python
wavs, sr = model.generate_voice_clone(
    text="I am solving the equation: x = [-b ± √(b²-4ac)] / 2a?",
    language="English",
    ref_audio=ref_audio,
    ref_text=ref_text,
)
```

`generate_voice_clone` 實際接受的參數逐項列出：

- `text`：字串或字串 list。
- `language`：字串或 list。
- `ref_audio`：參考音，可為本地檔案路徑、URL、base64 字串，或 `(numpy_array, sample_rate)` tuple。
- `ref_text`：參考音的逐字稿。
- `voice_clone_prompt`：可選，改用 `create_voice_clone_prompt(...)` 預建的可複用 prompt（替代 `ref_audio`／`ref_text`）。
- `x_vector_only_mode`：可選 bool（預設 `False`）。
- 另可傳入 Hugging Face `model.generate` 的通用生成參數（如 `max_new_tokens`、`top_p`）。

**無 `instruct`／`emotion`／`style` 任何情緒或語氣控制欄位。**
〔官方 primary source，README 原始檔〕 https://raw.githubusercontent.com/QwenLM/Qwen3-TTS/main/README.md
〔官方 primary source，clone 模型卡〕 https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base
〔官方 primary source，PyPI 套件說明〕 https://pypi.org/project/qwen-tts/

### 3.2 DashScope 託管 voice clone 的合成參數

託管路徑為「先註冊音色再合成」兩步：

- 註冊（voice-enrollment）：`model: "voice-enrollment"`，`input.action: "create_voice"`，附 `target_model`、`prefix`、參考音 `url`。
- 合成：以 `voice: "[voice_id]"` 指定已註冊音色。

託管 clone 合成呼叫**僅有 `voice` 一個音色指定參數，同一次呼叫無 emotion／instruct／style 欄位**。可作為 clone 目標的合成模型 id 包含 `qwen3-tts-vc-2026-01-22`、`qwen3-tts-vc-realtime-2026-01-15`、`cosyvoice-v2` 等。〔官方 primary source〕 https://www.alibabacloud.com/help/en/model-studio/voice-cloning-user-guide

（附註：`qwen3-tts-vc-2026-01-22` 的日期字串與 Qwen3-TTS 開源釋出日 2026-01-22 相符，可作為版本時序旁證。）

### 3.3 是否能在克隆同一次合成中控制情緒（主問題 3）

- 開源路徑：`generate_voice_clone` 函式簽章無情緒欄位（見 3.1）。
- 託管路徑：合成呼叫僅 `voice` 參數，無情緒欄位（見 3.2）。
- 官方模型能力表將 clone 模型（`*-Base`）的「Instruction Control」欄標為無（見 4）。

判定：**官方未提供在克隆同一次合成呼叫中控制情緒的機制。** 此判定由「函式簽章 + 託管 API 參數 + 能力表」三者一致推得；官方文件未見一句直述「clone 不支援 instruct」的明文，故嚴格而言屬「以能力表與 API 契約推得的不支援」，非官方單句宣告。未找到官方明文正面宣告 clone 支援情緒的任何來源。

---

## 4. 三路徑 instruct 支援對照（主問題 4）

官方 README 能力表（依原始檔擷取；load-bearing 的 Base 對比列另有 README 內文、模型卡與程式碼範例交叉佐證）：

| 模型 | 功能 | Instruction Control |
|---|---|---|
| Qwen3-TTS-12Hz-1.7B-VoiceDesign | 依文字描述生成音色 | ✅ |
| Qwen3-TTS-12Hz-1.7B-CustomVoice | preset 音色 + 風格控制（9 種 premium 音色） | ✅ |
| Qwen3-TTS-12Hz-1.7B-Base | 3 秒快速 voice clone | 無 |
| Qwen3-TTS-12Hz-0.6B-Base | 3 秒快速 voice clone | 無 |

〔官方 primary source〕 https://raw.githubusercontent.com/QwenLM/Qwen3-TTS/main/README.md

各路徑帶 `instruct` 的官方範例（逐字保留原文）：

`generate_custom_voice`（preset speaker + instruct，情緒可控）：
```python
wavs, sr = model.generate_custom_voice(
    text="其实我真的有发现，我是一个特别善于观察别人情绪的人。",
    language="Chinese",
    speaker="Vivian",
    instruct="用特别愤怒的语气说",
)
```

`generate_voice_design`（文字描述生成 + instruct）：
```python
wavs, sr = model.generate_voice_design(
    text="哥哥，你回来啦，人家等了你好久好久了，要抱抱！",
    language="Chinese",
    instruct="体现撒娇稚嫩的萝莉女声，音调偏高且起伏明显，营造出黏人、做作又刻意卖萌的听觉效果。",
)
```

〔官方 primary source，README 原始檔〕 https://raw.githubusercontent.com/QwenLM/Qwen3-TTS/main/README.md
〔官方 primary source，CustomVoice 模型卡〕 https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice

結論：`custom_voice` 與 `voice_design` 皆有 `instruct` 參數且官方能力表標記支援 Instruction Control；`voice_clone`（Base）兩者皆無。**專案「clone 不支援 instruct、preset／design 支援」的假設，經 primary source 佐證屬實。**

（可信度附註：上表由 WebFetch 擷取 README 產生。真正 load-bearing 的事實是「Base 無 Instruction Control，CustomVoice／VoiceDesign 有」，此點已由能力表 + README 內文 + 模型卡 + 三段程式碼範例交叉確認；0.6B-CustomVoice 是否具 Instruction Control 這類次要格位未逐一核對，不影響本研究結論。）

---

## 5. 社群鏈式做法：design→clone／情緒生成→clone（次問題 5）

### 5.1 鏈式做法是否被社群提出、能達成什麼

社群指南明確描述「先設計音色 → 由該音色建立可複用 clone prompt」的兩步工作流，用途是穩定 VoiceDesign 每次生成音色不一致的問題，同時保留設計彈性。此即 design→clone 鏈式的雛形。〔社群／非官方〕 https://ocdevel.com/blog/20260302-qwen-tts-voice-cloning

情緒可否忠實轉移：同一社群指南指出 clone 會把參考音的「音高範圍、節奏、呼吸模式」等韻律一併學走，並直言「單調的參考音會產出單調的克隆音，富表現力的片段則給模型更多韻律變化可用」；加上 `ref_text` 逐字稿可把 speaker similarity 由約 0.75 提升到約 0.89，因模型能同時對齊音色與韻律。**推論：以帶目標情緒的音檔作為 ref_audio，克隆音色會承接該情緒。**〔社群／非官方〕 https://ocdevel.com/blog/20260302-qwen-tts-voice-cloning

### 5.2 做不到的邊界

- 情緒被固定在參考音承載的單一語氣，指南強調 15 秒內參考音應保持情緒一致、避免模型需消歧多種語氣；文中**未描述任何逐句即時調整情緒的機制**。故鏈式做法只能達成「一段參考音 = 一種情緒」，要換情緒需換參考音重建 clone。〔社群／非官方〕 https://ocdevel.com/blog/20260302-qwen-tts-voice-cloning
- 官方 GitHub 有社群直接要求「為 voice clone 加入情緒／韻律控制（instruction-based 或 inline emotion tags）」，發起者明言目前「在克隆音色上處理 timing 與情緒有困難」；該討論**無 Qwen 團隊回覆、無替代方案被提出**。〔社群／非官方，官方 repo Discussion〕 https://github.com/QwenLM/Qwen3-TTS/discussions/218
- 另有社群提案「雙參考合成」（音色參考 + 韻律／情緒參考分離），發起者指出現況「clone 能抓住音色，但控制韻律與情緒需要一段完美的參考錄音，或難以微調的文字指令」；該討論同樣**無官方回覆、非現有功能**。〔社群／非官方，官方 repo Discussion〕 https://github.com/QwenLM/Qwen3-TTS/discussions/253

---

## 6. 其他社群替代方案：自訂音色 + 情緒可調（次問題 6）

1. 用 `generate_custom_voice`（preset speaker + `instruct`）取得「可逐次換情緒」的語氣控制，代價是音色限定官方 9 種 premium preset，非任意克隆音色。此為官方能力，非替代 hack。〔官方 primary source〕 https://raw.githubusercontent.com/QwenLM/Qwen3-TTS/main/README.md
2. 用 `generate_voice_design`（文字描述 + `instruct`）以自然語言同時指定音色與情緒，可得「自訂音色 + 語氣」但音色為描述生成、非對特定人聲的忠實克隆，且每次生成音色可能漂移（社群指出需靠 5.1 的 design→clone 固定）。〔官方 primary source〕 https://raw.githubusercontent.com/QwenLM/Qwen3-TTS/main/README.md ；漂移問題〔社群／非官方〕 https://ocdevel.com/blog/20260302-qwen-tts-voice-cloning
3. 「特定人聲克隆 + 逐句情緒可調」在同一次 clone 呼叫內達成：**未找到任何官方或社群公認可行的方案，無法證實**。社群 feature request（#218、#253）正是為此缺口而發，且無官方回應。〔社群／非官方〕 https://github.com/QwenLM/Qwen3-TTS/discussions/218

---

## 7. 對「spec 變更關卡」的裁決

1. 若「clone 音色的情緒控制」要求的是「同一次 clone 呼叫內、逐句可調的 instruct 情緒」：**官方不支援，且無社群可行替代**。維持現行「clone 不支援 instruct」的能力感知假設在 primary source 上正確。
2. 若可接受「一種克隆音色綁定一種固定情緒」：**社群鏈式做法（custom+instruct 或 design 產出目標情緒音檔 → 作為 clone 參考音）可行**，屬非官方觀察，情緒轉移品質未有官方保證，需自行驗證且無法逐句切換。
3. 任何開放設計若對外承諾「克隆音色 + 逐句情緒」，目前缺官方 API 支撐，屬高風險假設。

---

## 8. 來源清單

官方 primary source：
- QwenLM/Qwen3-TTS（原始碼庫）：https://github.com/QwenLM/Qwen3-TTS
- README 原始檔：https://raw.githubusercontent.com/QwenLM/Qwen3-TTS/main/README.md
- clone 模型卡 Qwen3-TTS-12Hz-1.7B-Base：https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base
- CustomVoice 模型卡：https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice
- PyPI `qwen-tts`：https://pypi.org/project/qwen-tts/
- Alibaba Cloud Model Studio 語音合成模型清單：https://www.alibabacloud.com/help/en/model-studio/tts-model/
- Alibaba Cloud Model Studio 模型總覽：https://www.alibabacloud.com/help/en/model-studio/models
- Alibaba Cloud Model Studio voice cloning 指南：https://www.alibabacloud.com/help/en/model-studio/voice-cloning-user-guide
- QwenLM/Qwen2-Audio（釐清音訊理解 vs TTS）：https://github.com/QwenLM/Qwen2-Audio 、 https://qwenlm.github.io/blog/qwen2-audio/

社群／非官方：
- ocdevel voice cloning 指南（鏈式做法與情緒轉移邊界）：https://ocdevel.com/blog/20260302-qwen-tts-voice-cloning
- 官方 repo Discussion #218（clone 情緒控制 feature request，無官方回覆）：https://github.com/QwenLM/Qwen3-TTS/discussions/218
- 官方 repo Discussion #253（雙參考合成提案，無官方回覆）：https://github.com/QwenLM/Qwen3-TTS/discussions/253
- Pandaily（Qwen3-TTS 與 CosyVoice 架構差異報導）：https://pandaily.com/alibaba-open-sources-qwen3-tts-model-suite-delivering-multilingual-ultra-low-latency-speech-generation
