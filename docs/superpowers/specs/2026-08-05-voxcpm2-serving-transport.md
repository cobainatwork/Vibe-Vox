# 研究：VoxCPM2 的生產服務化與 BFF 傳輸端點契約

日期：2026-08-05
研究問題：VoxCPM2（`openbmb/VoxCPM2`）在生產如何服務化、如何被 FastAPI BFF 呼叫。三個子題：(a) 傳輸選型（原生 Python 進程包薄 FastAPI 直產 wav，對比官方 vLLM-Omni 的 OpenAI 相容端點 `/v1/audio/speech`），各自成熟度與對「單張 48GB 已住著 VibeVoice-ASR vLLM 與 Qwen3-ForcedAligner」的影響；(b) 若走 OpenAI 端點，參考音與行內風格 `(emotion tone)` 如何傳、`voice` 如何對映本專案三型音色、端點吃不吃得下逐句風格；(c) 逐句 `generate()` 的串接方式與 streaming 支援。

可信度分級：每條結論標「primary」或「community」。primary 指官方 repo 的原始碼與其內附文件、官方模型卡、arXiv、官方 vLLM／vLLM-Omni 文件與原始碼。查不到者明寫「未查證」並附「怎樣才算查證」，不以合理推論填補。

查證方法註記：本文的行為性結論**以原始碼為準，不以文件表格為準**。所有 vLLM-Omni 與 VoxCPM 的檔案皆以 GitHub API 取回 `main` 分支的原始檔逐行閱讀（`Accept: application/vnd.github.raw`），而非讀取二手摘要或渲染後的網頁。凡文件與原始碼衝突者，本文採原始碼並把衝突逐條記在 §4。

本文承接 `2026-07-24-voxcpm-evaluation.md` §7.2 標「可信度中，需實測端點形狀」的待辦，並**推翻 #14 既有 comment（2026-08-03 產出、未合入 repo）的傳輸傾向與三項技術判斷**，理由逐條記於 §4。

---

## 1. 一句話結論

**採 vLLM-Omni 的 `/v1/audio/speech` 作為生產傳輸。** 決定性理由只有一條：我們要的能力在端點上**全部**可達且已逐條在原始碼與官方 e2e 測試裡查證（行內 `(...)` 風格、兩種 clone 模式、預建音色、streaming），端點唯一表達不出來的是「參考音 + 逐字稿 + 參考音隔離」的組合式 Hi-Fi 模式，而那個模式**本來就與逐句情緒互斥、已被本專案排除**，所以它的能力缺口對我們的成本是零；相對地，走原生進程要自己寫並長期擁有一台 TTS server 與一層併發控制，而那層併發在 prod 是已知必需品。

---

## 2. 兩個選項的比較表

| 面向 | A：薄 FastAPI 包原生 `voxcpm` 進程 | B：vLLM-Omni `/v1/audio/speech` |
| --- | --- | --- |
| 成熟度 | 官方 Python 套件，`generate()` / `generate_streaming()` 為模型原生契約、README 與 readthedocs 完整。但 **repo 內沒有任何官方 HTTP server**（`app.py` 是 Gradio demo），套件自標 `Development Status :: 3 - Alpha`。〔primary：`OpenBMB/VoxCPM` 檔案清單、`pyproject.toml`〕 | 官方 vLLM 專案的 omni 擴充。VoxCPM2 是**一等公民**：專屬 adapter、專屬 deploy yaml、專屬 recipe、offline 與 online 各自的 e2e 測試（含 streaming 與 4 併發）。〔primary：`vllm_omni/entrypoints/openai/tts_adapters/voxcpm2.py`、`vllm_omni/deploy/voxcpm2.yaml`、`recipes/OpenBMB/VoxCPM2.md`、`tests/e2e/online_serving/test_voxcpm2_tts{,_expansion}.py`〕 |
| 逐句風格 | 行內 `(emotion tone)` 前綴於 `text`。**這是唯一通道**：`_generate()` 全部參數裡沒有 `instruct`／`style`／`emotion`。一次呼叫一種風格。〔primary：`src/voxcpm/core.py:VoxCPM._generate`〕 | 同一條通道，且**等價**：`_build_voxcpm2_prompt` 把 `request.input` 原樣送去 tokenize，括號不被剝除。`instructions` 與 `task_type` 欄位對 VoxCPM2 **靜默無效**（見 §3.2.2）。一次 request 一種風格。〔primary：`serving_speech.py:_build_voxcpm2_prompt`〕 |
| 傳參考音 | `reference_wav_path`（reference 模式）／`prompt_wav_path` + `prompt_text`（continuation 模式）／三者併給（組合模式，官方建議的最高相似度做法）。皆為**本機檔路徑**。〔primary：`voxcpm2.py:VoxCPM2Model.build_prompt_cache` docstring〕 | `ref_audio`（`http(s)` URL／`data:` base64／`file://` 且需 `--allowed-local-media-path`）+ 選填 `ref_text`。伺服器端強制 1.0s ≤ 時長 ≤ 30.0s、自動降為單聲道。**per-request 拿不到組合模式**（見 §3.2.1）。〔primary：`serving_speech.py:_resolve_ref_audio`、`_REF_AUDIO_MIN_DURATION`／`_REF_AUDIO_MAX_DURATION`〕 |
| streaming | `generate_streaming()` yield chunk，自行 `np.concatenate`。**streaming 下 `retry_badcase` 被強制關掉**並發 warning，等於失去 badcase 重試這道保險。〔primary：`voxcpm2.py:_generate_with_prompt_cache`〕 | `stream=true` + `stream_format="audio"` 已由官方 e2e 測試（zero-shot 與 voice clone 各一）與 recipe 的 curl 實測涵蓋。SSE 與 WebSocket 路徑非 model-gated 故結構上可用，但**對 VoxCPM2 無任何官方驗證**。streaming 時 `speed` 必須為 1.0。〔primary：測試檔、`recipes/OpenBMB/VoxCPM2.md` T4；缺口見 §6〕 |
| 共卡 VRAM | 官方模型卡標 ~8 GB（bf16）。不預配 KV cache、**啟動時沒有任何記憶體檢查**，不夠就在推論期 OOM。〔primary：HF 模型卡；「~8 GB」為官方數字但本機未實測〕 | 權重 ~4.9 GiB + talker 的 CUDA-Graph／CFM／VAE 緩衝 ~2 GiB + **可顯式上限的 KV cache**（`kv_cache_memory_bytes`）。啟動時有硬性 gate：`free_memory < total_memory × gpu_memory_utilization` 即 `raise ValueError` 拒絕啟動，不會拖到推論期才炸。〔primary：`recipes/OpenBMB/VoxCPM2.md`、`vllm/v1/worker/utils.py:request_memory`〕 |
| 共卡耦合度 | 與 ASR 的 vLLM 無參數耦合。 | **也沒有參數耦合**：vLLM 的 `gpu_memory_utilization` 是 per-instance 上限，官方 docstring 明寫「另一個 vLLM 實例在同卡上並不影響它」；且設了 `kv_cache_memory_bytes` 之後 KV 尺寸**完全不看** `gpu_memory_utilization`。真正的約束是「本實例啟動當下的 free memory」。〔primary：`vllm/config/cache.py:CacheConfig`、`vllm/v1/worker/gpu_worker.py:determine_available_memory`〕 |
| 併發／吞吐 | 無。單進程序列化，要併發得自寫佇列與 worker 池。 | 上游 vLLM 排程器：continuous batching，官方 e2e 測試已跑 4 併發（`max_num_seqs: 8`）。 |
| 冷啟成本 | `optimize=True`（預設）在 `__init__` 內做 torch.compile 並跑一次暖機 `generate`。 | server 冷啟 ~60s，另有首請求 ~25s 的 compile／JIT／graph capture；`warmup()` 已把其中約 15s 挪到啟動期。穩態 RTF ~0.12。〔primary：recipe〕 |
| 我們要寫與維護的東西 | 一台 FastAPI server、佇列、健康檢查、暖機、容器。容器要吞下 `voxcpm` 的依賴樹（`gradio>=6,<7`、`modelscope`、`funasr`、`datasets`、`matplotlib`）。〔primary：`pyproject.toml`〕 | 一個 `TtsBackend` 實作（HTTP client + 幾個非標準欄位）。server 由官方鏡像／`vllm serve` 提供。 |
| 消費端後處理 | 48kHz mono → 24kHz／mono／16-bit。 | **完全相同**，端點也回 48kHz mono。〔primary：recipe T1／T4、`text_to_speech.md`〕 |

兩條路的取樣率、行內風格語法、以及「逐句不同情緒 = 逐句一次呼叫」三件事**完全一致**，不構成選型依據。

---

## 3. 逐項發現

### 3.1 子題 (a)：傳輸選型與共卡影響

**3.1.1 官方確實提供 VoxCPM2 的 drop-in OpenAI 相容端點，啟動指令為 `vllm serve openbmb/VoxCPM2 --omni`。**〔primary〕
deploy config 依 HF `model_type=voxcpm2` 自動載入 `vllm_omni/deploy/voxcpm2.yaml`，可用 `--deploy-config` 覆寫、`--stage-N-<field>` 逐 stage 微調。`--trust-remote-code` 非必要（recipe 明述；但 yaml 內設了 `trust_remote_code: true`，e2e 測試也帶了該旗標）。
出處：`docs/user_guide/examples/online_serving/text_to_speech.md` §VoxCPM2、`recipes/OpenBMB/VoxCPM2.md`、`vllm_omni/deploy/voxcpm2.yaml`。

**3.1.2 VoxCPM repo 內不存在官方 HTTP server，選項 A 的 server 是 100% 我們自己的程式碼。**〔primary〕
`OpenBMB/VoxCPM` 的 `main` 分支頂層只有 `app.py`／`app_old.py`（`import gradio as gr`，Gradio demo）與 `lora_ft_webui.py`；套件入口是 CLI（`voxcpm = "voxcpm.cli:main"`）。沒有 `server.py`／FastAPI／OpenAI 相容層。
出處：`OpenBMB/VoxCPM` git tree（`main`，recursive）、`app.py`、`pyproject.toml`。

**3.1.3 官方 README 把「production deployment」指向兩處，其中一處是第三方。**〔primary + community〕
README 的 Production Deployment 段落指向 `a710128/nanovllm-voxcpm`（Nano-vLLM-VoxCPM），Production Serving 段落指向 vLLM-Omni。前者為第三方 repo（283 stars，14 open issues，最近 push 2026-08-03），授權為 **MIT**（GitHub 的授權偵測回報 `NOASSERTION`，是因為 `LICENSE` 檔在 MIT 全文前多了一行標題，實際內容為標準 MIT 全文，已逐字確認）。它自述 FastAPI 部分是「optional FastAPI **demo**」且未上 PyPI，另有一則 Known Issue（部分 VoxCPM release 以 `.pt` 落盤，nanovllm 只吃 `.safetensors`）。作為第三方選項可列為備案，不建議作為主線。
出處：`OpenBMB/VoxCPM/README.md`、`a710128/nanovllm-voxcpm/{README.md,LICENSE}`、GitHub API。

**3.1.4 vLLM-Omni 的 VoxCPM2 voice-clone 曾有「decoder 不發 stop token、輸出恆數分鐘」的 bug，已修，且修補就在我們讀到的現行程式碼裡。**〔primary〕
issue #2896（`[Bug]: VoxCPM2 voice-cloning decoder never emits stop token, output always ~5 min`）已 closed；修補 PR #2894（`[Bugfix][VoxCPM2] Fix voice-clone decode loop by padding prefill prompt`）的 root cause 是 talker 預期的 prefill 長度（`ref_feat_len + text_len + 1`，`ref_continuation` 更長）與 vLLM 側送進去的 prompt 長度不一致。現行 `build_voxcpm2_prompt` 正是以 `prompt_token_ids=[1] * prefill_len` 補齊，且 `prefill_len` 逐模式累加 `ref_len`／`len(ref_ids)`／`+2`（ref_start／ref_end），即該修補已在 `main`。
出處：vllm-omni issues #2896／#2894、`vllm_omni/model_executor/models/voxcpm2/voxcpm2_talker.py:build_voxcpm2_prompt`。

**3.1.5 另有一則已修的 streaming 截尾 bug 波及 VoxCPM，且現行 deploy yaml 把觸發它的路徑關掉了。**〔primary〕
issue #3090：`OmniGenerationScheduler` 在 stateful streaming vocoder 的 finalize chunk 送出前就關掉 request（`async_chunk` 路徑，影響 CosyVoice3 與 VoxCPM），已 closed。現行 `voxcpm2.yaml` 首行群組即 `async_chunk: false`。這意味著 VoxCPM2 預設**不走** async-chunk pipelining，首包延遲的優化空間與 Qwen3-TTS 不同。
出處：vllm-omni issue #3090、`vllm_omni/deploy/voxcpm2.yaml`。

**3.1.6 共卡：真正的約束是「啟動當下的 free memory」，不是「兩個 vLLM 的 utilization 相加」。**〔primary〕
三段官方原始碼決定這件事：

1. `vllm/config/cache.py:CacheConfig.gpu_memory_utilization` 的 docstring 逐字：「This is a per-instance limit, and only applies to the current vLLM instance. It does not matter if you have another vLLM instance running on the same GPU. For example, if you have two vLLM instances running on the same GPU, you can set the GPU memory utilization to 0.5 for each instance.」
2. `vllm/v1/worker/utils.py:request_memory`：`requested_memory = ceil(total_memory × gpu_memory_utilization)`，接著 `if init_snapshot.free_memory < requested_memory: raise ValueError(...)`，訊息為「Free memory on device ... on startup is less than desired GPU memory utilization ... Decrease GPU memory utilization or reduce GPU memory used by other processes.」
3. `vllm/v1/worker/gpu_worker.py:Worker.init_device` **無條件**呼叫 `request_memory()`，且是在 KV cache 決策之前；而 `determine_available_memory()` 一旦看到 `kv_cache_memory_bytes` 就**跳過 memory profiling**，log 逐字「This does not respect the `gpu_memory_utilization` config」。`CacheConfig.kv_cache_memory_bytes` 的 docstring 亦逐字：「kv_cache_memory_bytes (when not-None) ignores gpu_memory_utilization」。

推論（標明為推論）：因此在本機上啟動 vLLM-Omni 時，`gpu_memory_utilization` 只剩「啟動 gate 的分母」這一個作用（必須 ≤ free/total），KV 尺寸由 `kv_cache_memory_bytes` 決定。**兩個 server 不需要「共同壓 utilization 使總和 < 1.0」**，這是 #14 既有 comment 的錯誤判斷。

**3.1.7 現行 `voxcpm2.yaml` 的預設值在本機上一定起不來，兩個記憶體參數都得改。**〔primary + 本機實測值〕
yaml 預設：`gpu_memory_utilization: 0.9`、`kv_cache_memory_bytes: 6442450944`（6 GiB）、`max_num_seqs: 8`、`max_model_len: 4096`、`enforce_eager: true`、`enable_prefix_caching: false`。yaml 註解自述這組值「Keeps peak ~13 GiB on any card」。recipe 的 4090 實測是另一組較舊的值（`max_num_seqs: 4`、KV ~15.2 GiB、resident ~22 GiB / 24 GiB），與現行 yaml 已不一致，**recipe 的記憶體數字不可拿來規劃本機容量**。

本機已量到的（`HANDOFF.md` §8.2，2026-08-05，帶 `gpu_uuid` 且 vLLM 完全啟動後）：vLLM ASR 33654 MiB、aligner 3620 MiB；GPU 0 總量 46068 MiB（`nvidia-smi`）／45465 MiB（torch）。

由 §3.1.6 的 gate 直接得出兩條硬性要求（推論，算式明列）：
- `gpu_memory_utilization` 必須 ≤ 啟動當下的 `free/total`。以 torch 的 45465 MiB 為分母、ASR 與 aligner 已在位為前提，該比值約 0.18，**遠低於 yaml 的 0.9**。
- `kv_cache_memory_bytes` 的 6 GiB 加上權重 ~4.9 GiB 與 talker 緩衝 ~2 GiB 已超過剩餘空間，必須下調。

**但餘裕的精確值算不出來，也不該算。** `HANDOFF.md` §8.2 已明記三個理由：VoxCPM2 的 ~8192 MiB 是估算而非實測、aligner 的佔用會隨 PyTorch caching allocator 長高且穩態上限未知、GPU 0 的總量本身有 46068 與 45465 兩個值而其 603 MiB 差距已大於任何算得出來的餘裕。本文遵守該結論：**不給餘裕數字，只給「哪兩個旋鈕要動」與「要量什麼」**（見 §6）。

**3.1.8 記憶體不是選型的判別依據。**〔推論，基於上列 primary 數字〕
選項 A 是官方模型卡的 ~8 GB 估算、無上限機制、不夠就在推論期 OOM；選項 B 是 ~4.9 GiB 權重 + ~2 GiB 緩衝 + 顯式上限的 KV，且不夠就在啟動期明確拒絕。兩者都落在同一個「約 8 GiB 剩餘空間」的邊界上，差別在 B 的消耗可被明文封頂、失敗發生在啟動而非服務中。#14 既有 comment 稱原生進程「餘裕大、記憶體預算單邊即可」在對照實際數字後不成立。

### 3.2 子題 (b)：端點如何傳參考音與行內風格、`voice` 如何對映三型音色

**3.2.1 參考音走 `ref_audio`（+ 選填 `ref_text`），而「有沒有給 `ref_text`」直接決定落到哪一種 clone 模式。**〔primary〕
`build_voxcpm2_prompt` 的分支逐行為：

- `ref_audio` 有、`ref_text` 為 `None` → `additional["reference_audio"] = [[ref_audio, ref_sr]]`，`prefill_len += ref_len + 2`（ref_start／ref_end）。即**reference 模式 = Controllable Cloning，吃行內風格**。
- `ref_audio` 有、`ref_text` 也有 → `additional["prompt_audio"]` + `additional["prompt_text"]`，`prefill_len += ref_len + len(ref_ids)`。即**continuation 模式 = Hi-Fi／Ultimate Cloning，不吃行內風格**。
- 兩者皆無、`voice` 命中預運算 profile → 走 `voice_profile`，模式由 profile 的 `mode` 欄位決定（`reference`／`continuation`／`ref_continuation`）。
- 全都沒有 → zero-shot。

「Hi-Fi 不吃風格」有三重 primary 佐證，且第三重解釋了機制：readthedocs 逐字「When Hi-Fi mode is enabled, the control instruction is ignored」；官方 CLI 直接**拒絕**該組合（`src/voxcpm/cli.py`：`if args.control and prompt_text: parser.error("--control cannot be used together with --prompt-text or --prompt-file.")`）；而 `voxcpm2.py:_generate_with_prompt_cache` 在 continuation 模式下做的是 `text = prompt_text + target_text`，所以放在 `target_text` 開頭的 `(...)` 會落到串接後字串的**中段**，不再是前綴。

**per-request 拿不到組合模式。** README 的 Ultimate Cloning 範例把同一段音檔同時給 `prompt_wav_path` 與 `reference_wav_path`（註「optional, for better similarity」），`build_prompt_cache` 的 docstring 也明列「all three -> combined ref + continuation mode」。但端點的 inline 分支是 if／else，`ref_audio` + `ref_text` 只會進 continuation。組合模式在端點上**只能經預運算 profile 的 `mode=ref_continuation` 取得**。
出處：`voxcpm2_talker.py:build_voxcpm2_prompt`、`serving_speech.py:_build_voxcpm2_prompt`、`voxcpm2.py:{build_prompt_cache,_generate_with_prompt_cache}`、`cli.py`、`OpenBMB/VoxCPM/README.md`、https://voxcpm.readthedocs.io/en/latest/usage_guide.html

**3.2.2 行內風格走 `input`；`instructions` 與 `task_type` 對 VoxCPM2 是靜默無效欄位。**〔primary，這是本文最重要的單一發現〕
`_build_voxcpm2_prompt` 傳給 `build_voxcpm2_prompt` 的參數只有 `hf_config`、`tokenizer`、`split_map`、`text=request.input`、`ref_audio`、`ref_sr`、`ref_text`、`voice_profile`。**`request.instructions` 與 `request.task_type` 一次都沒被讀。** 而 `VoxCPM2Adapter.validate()` 只檢查三件事（`input` 非空、`voice` 在可用集合內、`max_new_tokens` 在 1..4096），**不會**因為帶了 `instructions` 或 `task_type` 而回錯。整份 `serving_speech.py` 裡對這兩個欄位的拒絕只出現在 `_validate_ming_flash_omni_tts_request`（Ming-flash-omni 專屬），與 VoxCPM2 無關。

結果：帶 `instructions="cheerful"` 的請求會回 HTTP 200 與一段**不帶該情緒**的音訊，沒有任何錯誤或警告。這是 BFF 必須自己擋掉的坑。

風格的唯一通道是把 `(...)` 寫進 `input`：`build_voxcpm2_prompt` 對 `text` 只做 `split_multichar_chinese(tokenizer.encode(text, add_special_tokens=True), split_map)`，括號不被剝除，原樣進 text token 串。官方 CLI 的 `--control` 也正是這麼實作的：`build_final_text(text, control) -> f"({control}){text}"`。
出處：`serving_speech.py:_build_voxcpm2_prompt`、`tts_adapters/voxcpm2.py:VoxCPM2Adapter.validate`、`voxcpm2_talker.py:build_voxcpm2_prompt`、`src/voxcpm/cli.py:build_final_text`、`protocol/audio.py:OpenAICreateSpeechRequest`（`instructions` 的 description 逐字為「maps to 'instruct' for Qwen3-TTS」，`task_type` 位於註解 `# Qwen3-TTS specific parameters` 之下）。

**3.2.3 `voice` 欄位對映三型音色。**〔primary〕
先確立一件事：**VoxCPM2 沒有內建語者。** `serving_speech.py:_load_supported_speakers` 對 `voxcpm2` 直接 `return {"default"}`；`warmup()` 的註解逐字「VoxCPM2 has no predefined speaker presets. "default" means zero-shot mode (no voice cloning). The voice field is required by the OpenAI API schema but semantically ignored by the model.」；官方範例 client 與 e2e 測試一律送 `voice: "default"`。

| 本專案音色型別 | 端點對映 | 依據 |
| --- | --- | --- |
| 系統預建 Voice（原 Preset speaker） | 兩條路皆可。(i) **預運算 profile**：離線跑 `examples/online_serving/text_to_speech/voxcpm2/precompute_custom_voice.py --voice-name alice --ref-audio ... --mode {reference,continuation,ref_continuation} [--prompt-text ...]`，產出 `custom_voice_manifest.json` + 每音色一個 `.safetensors`，於 deploy config 設 `custom_voice_dir`，之後 `voice="alice"` 免帶 `ref_audio`。(ii) **上傳音色**：`POST /v1/audio/voices`（multipart，必填 `audio_sample`／`consent`／`name`，選填 `ref_text`／`speaker_description`，上限 10MB），落盤為 `.safetensors`、重啟自動還原、同名覆寫。 | `precompute_custom_voice.py`、`speech_api.md` §Precomputed Custom Voices／§Voices Endpoint、`tts_adapters/voxcpm2.py:VoxCPM2Adapter.build`（讀 `server.uploaded_speakers` 與 `server.precomputed_speakers`） |
| Voice clone | per-request `ref_audio`（要逐句情緒就**不要**給 `ref_text`），或預先上傳／預運算後以 `voice="<name>"` 引用。 | §3.2.1 |
| Voice design | `input="(描述)要合成的文字"`、**不帶** `ref_audio`、`voice="default"` → zero-shot 分支。**不需要也不能靠 `task_type=VoiceDesign`**（該欄位對 VoxCPM2 無效，見 §3.2.2）。ADR-0002 的「定版」在此仍成立：跑一次、把輸出存為參考音，之後走 clone 路徑重播。 | `OpenBMB/VoxCPM/README.md` §Voice Design（「put the description in parentheses at the start of `text`」）、§3.2.2 |

三點實作細節：`voice` 會被 `request.voice.lower()` 正規化後比對，不在集合內回錯並列出可用值；若 `voice` 指向一個以「直接 speaker embedding」上傳的音色，adapter 會 `raise ValueError`（該形式僅 Qwen3 可用，VoxCPM2 需要音檔）；`ref_audio` 與 `voice` 同時給時，`ref_audio` 優先（`if request.ref_audio is None` 才載入上傳音色）。

**3.2.4 端點吃得下逐句風格，但單位是「一次 request 一種風格」。**〔primary〕
風格既然是 `input` 的行內前綴，一次 request 就只能有一種。逐句不同情緒 = 逐句切分後每句一次 request。這與選項 A 完全相同（`_generate()` 對整段 `text` 套用單一前綴），也正好對齊消費端的回合／逐句模型。

另有一條未被 #14 既有 comment 提到的路徑：`/v1/audio/speech/stream`（WebSocket）接受漸進送入的文字，`session.config` 為 sticky、`input.done` 是 flush 而非斷線，一條連線可服務多輪。理論上每次 flush 的文字可自帶自己的 `(...)` 前綴，即可在單一連線上做逐句風格。但官方文件同時說明「An utterance is the flush unit, not a linguistic one... every utterance reports `sentence_index: 0` of `total_sentences: 1`」，即該端點**不會**幫你切句；且 VoxCPM2 走這條路沒有任何官方驗證（見 §6）。
出處：`speech_api.md` §Streaming Text Input (WebSocket)、`serving_speech_stream.py`、`api_server.py`（`@router.websocket("/v1/audio/speech/stream")`）。

**3.2.5 不需要為情緒或參考音擴充 schema，但要接受這些是非標準欄位。**〔primary〕
`ref_audio`／`ref_text`／`task_type`／`instructions`／`stream_format`／`max_new_tokens` 等皆為 vLLM-Omni 對 OpenAI schema 的擴充欄位，用官方 OpenAI SDK 呼叫時須以 `extra_body` 帶入；直接以 `httpx` POST JSON 則無此問題（官方範例 client 就是用 `httpx`）。ADR-0001 當初「標準 `/v1/audio/speech` 欄位不足」的判斷對**純標準** schema 仍然正確，但 vLLM-Omni 的擴充已把參考音補上；風格則從頭到尾不需要欄位，因為它在文字裡。

### 3.3 子題 (c)：逐句 `generate()` 的串接與 streaming

**3.3.1 原生逐句串接的正確做法是「prompt cache 建一次、逐句重用」，而公開的 `generate()` 做不到。**〔primary〕
`VoxCPM._generate()` 每次呼叫都會在內部呼叫 `self.tts_model.build_prompt_cache(...)`，也就是**每一句都重新編碼一次參考音**。`build_prompt_cache()` 與 `_generate_with_prompt_cache()` 是 `VoxCPM2Model` 上的獨立方法，一次建 cache、多次生成在模型層是支援的（`_generate_with_prompt_cache` 的 `prompt_cache` 參數 docstring 逐字「Cache built by `build_prompt_cache()`. Can be None for zero-shot generation.」）。因此選項 A 若要避免逐句重編參考音，必須繞過公開 API、直接用 `model.tts_model.build_prompt_cache()` + `model.tts_model._generate_with_prompt_cache()`，即**依賴帶底線的私有方法**。這是選項 A 的一項隱性維護成本。

作為對照，端點側的等價機制是官方支援的：預運算 profile 與上傳音色的特徵抽取結果都進共用 LRU（512 MiB 預算），「repeated requests with the same `voice=...` skip the extraction pipeline」；per-request `ref_audio` 也有 `_ref_audio_resolve_cache` 以 URL 的 SHA-1 為鍵做解析快取。
出處：`src/voxcpm/core.py:VoxCPM._generate`、`voxcpm2.py:{build_prompt_cache,_generate_with_prompt_cache}`、`speech_api.md` §Voice Storage & Caching、`serving_speech.py:_resolve_ref_audio`。

**3.3.2 原生 streaming 存在，代價是失去 badcase 重試。**〔primary〕
`generate_streaming()` 與 `generate()` 共用 `_generate(streaming=...)`，前者回 generator、後者回單一 `np.ndarray`。`_generate_with_prompt_cache` 開頭即：`if retry_badcase and streaming: warnings.warn("Retry on bad cases is not supported in streaming mode, setting retry_badcase=False."); retry_badcase = False`。而 `retry_badcase` 預設為 `True`，其判準是 audio/text 比值超過 `retry_badcase_ratio_threshold=6.0` 就重生（最多 3 次）。逐句合成正是最容易踩到長度爆走的情境，所以「逐句 + streaming」在選項 A 下等於自願放棄這道保險。

**3.3.3 端點 streaming 已由官方 e2e 測試涵蓋，但只涵蓋 `stream_format="audio"`。**〔primary〕
`tests/e2e/online_serving/test_voxcpm2_tts.py:test_text_to_audio_002`（zero-shot、`stream=True`、`stream_format="audio"`、`response_format="wav"`）與 `test_voxcpm2_tts_expansion.py:test_voice_clone_streaming_001`（同上再加 `ref_audio`，4 併發）皆為真模型推論（`pytest.mark.full_model`，L4 單卡）。recipe T4 另有一條可直接複製的 curl（`stream: true`、`stream_format: "audio"`、`response_format: "pcm"`，播放器 `-r 48000`）。

SSE（`stream_format="sse"`，事件 `speech.audio.delta`／`done`／`error`，`done` 帶 token usage）與 WebSocket 的產生器與路由**都不是 model-gated**，結構上對 VoxCPM2 可用；但兩者對 VoxCPM2 皆無官方範例、測試或 recipe 佐證，列為缺口。另外 `docs/user_guide/.../text_to_speech.md` 的能力表把 VoxCPM2 的 Streaming 欄標成「✓（AudioWorklet via gradio）」，那只是在描述附帶的 demo 播放器，不是說 streaming 只能經 Gradio。

**3.3.4 兩條路都回 48kHz mono，adapter 的降採樣工作一模一樣。**〔primary〕
原生：`model.tts_model.sample_rate = audio_vae.out_sample_rate`（README 範例以它寫檔）。端點：recipe T1「5.12 s @ 48 kHz mono」、T4「230400 int16 samples = 4.80 s of PCM at 48 kHz... The player sample rate is 48 kHz, not 24 kHz」、e2e 測試註解「~0.5 s of 48 kHz mono PCM_16 in WAV」。消費端契約要 24kHz／mono／16-bit（ADR-0003），故 adapter 兩條路都要做 48k→24k 降採樣。

---

## 4. 推翻的既有判斷

本文與 #14 既有 comment（2026-08-03，未合入）在四處相反，逐條列出以免默默覆蓋：

| 既有判斷 | 本文結論 | 依據 |
| --- | --- | --- |
| 傳輸傾向原生進程，理由之一是「原生逐句 `generate()` + 行內 `(emotion)` 契約最直接」 | 端點以**完全相同**的方式承載行內風格（`input` 原樣 tokenize），此項不構成原生的優勢 | §3.2.2 |
| 「端點上究竟用 `instructions` 還是行內 `(emotion)` 吃風格」為最大缺口，需實測 | 已解：`instructions` 對 VoxCPM2 **從未被讀取**且不會報錯，只有行內一路 | §3.2.2 |
| 「`task_type=VoiceDesign` 對 VoxCPM2 是否成立」需實測 | 已解：不成立也不需要，Voice design = 行內描述 + 不帶 `ref_audio` | §3.2.3 |
| 「端點 streaming 是否受限、訊號矛盾」需實測 | 已解（部分）：`stream_format="audio"` 有官方 e2e 測試與 recipe curl 佐證；SSE／WS 仍是缺口 | §3.3.3、§6 |
| 「與 vLLM ASR 同卡須雙邊共同壓 `gpu_memory_utilization` 使總和 < 1.0」 | 錯。該參數是 per-instance 上限，官方 docstring 明寫另一個實例不影響它；且設了 `kv_cache_memory_bytes` 後 KV 尺寸完全不看它。真正的約束是啟動當下的 free memory | §3.1.6 |
| 「原生 ~8GB、共存餘裕大，記憶體預算單邊即可」 | 記憶體不是判別依據：兩條路都落在同一個約 8 GiB 邊界，差別在 B 可封頂且失敗在啟動期 | §3.1.7、§3.1.8 |
| 「vLLM-Omni 有 voice-clone EOS bug 前科」列為選型負分 | bug 已修，且修補（prefill padding）就在現行 `main` 的 `build_voxcpm2_prompt` 裡，可逐行確認 | §3.1.4 |

另有三處**官方文件自身互相矛盾**，本文一律採原始碼：

1. `docs/serving/speech_api.md` §Supported Models 稱 VoxCPM2「TTS + voice cloning with **built-in speaker presets**」。與原始碼（`_load_supported_speakers` → `{"default"}`）、`warmup()` 註解、範例 client 註解、recipe 與 e2e 測試註解全部相反。**VoxCPM2 沒有內建語者。**
2. `docs/user_guide/.../text_to_speech.md` 能力表把 VoxCPM2 的「Voice presets / upload」標為「—」。但原始碼的 `VoxCPM2Adapter.build` 明確處理 `server.uploaded_speakers`，`_load_precomputed_speakers` 也把 `voxcpm2` 列入支援。**上傳與預運算音色是可用的**，只有「內建 presets」不存在。
3. `recipes/OpenBMB/VoxCPM2.md` 的記憶體數字（`max_num_seqs: 4`、KV ~15.2 GiB、~22 GiB resident）對應的是較舊的 `voxcpm2.yaml`；現行 yaml 是 `max_num_seqs: 8` + `kv_cache_memory_bytes: 6 GiB` + 自述 peak ~13 GiB。**recipe 的記憶體數字已過期。**

版本落差也要記一筆：`OpenBMB/VoxCPM` README 的 vLLM-Omni 安裝指引釘 `vllm==0.19.0`，而 recipe 實測環境是 vLLM 0.21.0 / vLLM-Omni 0.20.1.dev。vllm-omni 的 `pushed_at` 是 2026-08-05（本文撰寫當日），移動速度很快，**部署時必須釘死版本並記錄**。

---

## 5. 對下游的影響

### 5.1 `TtsClient` adapter（`bff/src/vibe_vox/adapters/`）

現況：`base.py:TtsClient` 只有 `health()` 一個方法（註解已標「synthesize()（其對應票）隨各票加入本介面」），`stub.py:StubTtsClient` 同樣只有 `health()`。也就是說這裡還是白紙，沒有既有形狀要保護。落地時的具體結論：

1. **不需要 `TtsBackend` 抽象層。** 既有的三個 client（`AsrClient`／`AlignerClient`／`TtsClient`）都是「一個 Protocol + 一個真實作 + 一個 stub」的形狀（`vllm_asr.py`、`aligner.py`、`stub.py`），`TtsClient` 應照抄。既有 comment 建議的「`TtsBackend` 介面 + `NativeVoxcpmBackend`／`VllmOmniBackend` 兩實作」是為了在兩個傳輸間切換而付的抽象稅，而本文已收斂到單一傳輸，該抽象沒有第二個消費者，不要建。
2. **`synthesize()` 的簽章要能表達逐句。** 因為一次 request 只能一種風格，而消費端契約 `/api/tts/speech` 收的是整段文字加可選 Instruction，切句與逐句組風格前綴的責任落在 adapter（或其上一層）。介面應收「已切好的句子與各自風格」而非「一整段文字」，把切句決策留在 adapter 之外，才不會把中文斷句規則埋進 HTTP client。
3. **adapter 必須擋掉 `instructions`。** 端點對 VoxCPM2 靜默忽略 `instructions`（§3.2.2），BFF 若把 Instruction 放進該欄位會得到「HTTP 200 但沒有情緒」。正確做法是在 adapter 內把 Instruction 組成 `(...)` 前綴塞進 `input`，並且**不要**送 `instructions` 或 `task_type`（送了無害但會誤導後人）。
4. **adapter 內做 48kHz→24kHz／mono／16-bit。** 兩條傳輸都一樣，且這是 ADR-0003 對消費端的約束。
5. **參考音的傳法選 `data:` base64，不要 `file://`。** `file://` 需要 server 開 `--allowed-local-media-path`，會擴大 vLLM 容器的檔案系統暴露面；`data:` URI 沒有這個代價。同時 adapter 應在送出前自行驗 1.0s ≤ 時長 ≤ 30.0s，因為超界時端點回的是 `ValueError` 文字，不是我們的錯誤碼。
6. **Voice clone 的建立要決定走哪條註冊路徑**（per-request `ref_audio` / `POST /v1/audio/voices` 上傳 / 離線預運算）。三條都可行且可混用；`POST /v1/audio/voices` 需要一個 `consent` 欄位（必填），這是我們的 Voice CRUD 目前沒有的概念。

### 5.2 新 ADR（取代 ADR-0001，#19）

ADR-0001 現行三句話裡有兩句要改：

- 「Qwen3-TTS 由獨立服務程序提供」→ 引擎改 VoxCPM2，且**傳輸改為 vLLM-Omni 的 OpenAI 相容端點**。「解耦模型服務」的核心決策（模型不內嵌 BFF、各能力獨立服務、FastAPI 只做編排）**仍然成立且應保留**。
- Consequences 第一句「TTS 的 clone/design/instruct 走原生 `qwen-tts` API 或非標準擴充端點（`/v1/audio/speech` 標準欄位不足）」→ 要改寫為：走 vLLM-Omni 的 `/v1/audio/speech`；參考音經非標準擴充欄位 `ref_audio`／`ref_text`；風格不經任何欄位而是行內 `(...)`；`instructions` 與 `task_type` 對 VoxCPM2 無效。
- Consequences 第二句「必須壓低 vLLM 的 `gpu_memory_utilization`（約 0.55–0.6）替 TTS 留出 VRAM」→ **這句在事實與機制上都要重寫**。事實面：`HANDOFF.md` §8.3 已記該假設長期未被實作（實際跑上游預設 0.8，2026-08-05 才顯式設為 0.70），且現況是兩張卡、第二張被別的專案動態佔用。機制面：見 §3.1.6，`gpu_memory_utilization` 是 per-instance 上限而非全卡瓜分，第二個 vLLM 實例的約束是「啟動當下的 free memory ≥ total × 自己的 utilization」，而 KV 尺寸應以 `kv_cache_memory_bytes` 顯式封頂。新 ADR 應記的是「三個服務、每個各自的記憶體旋鈕與實測值」，而不是「壓低一個比例替另一個留空間」。

新 ADR 還應納入 ADR-0001 的 Considered Options 裡那條「純用現成 OpenAI-Compatible server、不做自己的後端」的**部分翻案**：它被否決的理由（標準 schema 承載不了 Hotwords／音色 CRUD／持久化）依然對，所以 BFF 仍要存在；但 TTS 這一段的推論（`/v1/audio/speech` 欄位不足所以要自寫服務）已被 vLLM-Omni 的擴充欄位推翻。

### 5.3 消費端契約（ADR-0003 與 #18）

- `/api/tts/speech` 對外形狀**不變**，傳輸選型對消費端透明。串流回應仍可提供（端點的 `stream_format="audio"` 已驗證）。
- `GET /api/tts/voices` 的 `preset_voices` 語意變更：VoxCPM2 無內建語者，「系統預建 Voice」是我方以 `precompute_custom_voice.py` 或上傳建立的唯讀音色集合。這是 CONTEXT.md 與 `docs/spec.md`（§「內建 9 個 Preset speaker」、user story 36／44）必須同步的領域模型變更，屬 #13 map 已定骨架的落地項。
- **能力感知規則要反向重寫。** CONTEXT.md 現行 Instruction 詞條寫「僅 Preset speaker 與 Voice design 生效，Voice clone 無效」，那是 Qwen3-TTS 語意。VoxCPM2 的分界不在音色型別，而在**該音色以哪一種 clone 模式重播**：reference 模式（只有參考音）支援行內風格，continuation 模式（參考音 + 逐字稿）忽略它。同一個 Voice clone 音色，我們選擇不送 `ref_text` 就能吃風格。所以正確的規則是「建立 Voice clone 時若要支援 Instruction，就不要把逐字稿餵進合成路徑」，而 UI 的停用條件應由「音色型別」改為「該音色的重播模式」。CONTEXT.md 的 Instruction 詞條同時要移除「對應模型的 `instruct` 參數」這句，VoxCPM2 沒有這個參數。
- ADR-0002（Voice design 建立時定版）**仍成立**，但多一條實作約束：定版擷取的參考音要以**原生 48kHz** 存檔，不能拿已降採樣成 24kHz 的消費端輸出去當參考音，否則每次重播都在複製一次降採樣損失。
- Voice clone 的參考音規格要寫進對外契約：時長 1.0–30.0 秒為端點硬性限制（實務建議 5–30 秒），上傳走 `POST /v1/audio/voices` 時另有 10MB 上限。

### 5.4 部署面（`docker-compose.yml`）

- vLLM-Omni 的上傳音色預設落在 `~/.cache/vllm-omni/speakers`（`SPEAKER_SAMPLES_DIR`，上限 `SPEAKER_MAX_UPLOADED=1000`）。若採上傳路徑，該目錄需要 persistent volume，否則容器重建就掉音色。
- 若採預運算路徑，`custom_voice_dir` 指向的目錄要 mount 進去，且該目錄由離線腳本產生（腳本本身需要 GPU 與 `voxcpm` 套件）。
- 現行 compose 把三個 GPU 服務全釘在 `device_ids: ["0"]`（`HANDOFF.md` §7），TTS 進來是第四個；GPU 1 被非 Vibe-Vox 的 gpustack 工作負載動態佔著，其餘裕不可假設穩定。

---

## 6. 未查證的缺口

1. **VoxCPM2 在本機的實際 VRAM 佔用。** 官方模型卡的 ~8 GB 是原生進程的估算，recipe 的 ~22 GiB 對應過期的 yaml，現行 yaml 自述 peak ~13 GiB。三個數字沒有一個能拿來規劃本機容量。**怎樣才算查證**：把 vLLM-Omni 起在 GPU 0（`gpu_memory_utilization` 先給 0.17 附近、`kv_cache_memory_bytes` 先給 1 GiB），等它完全啟動後帶 `gpu_uuid` 量 `nvidia-smi`，再跑一次含 `ref_audio` 的請求量峰值。同時量 aligner 在 batch 8 跑完長音檔的穩態佔用。三個數字擺在一起才知道夠不夠。**這件事歸 #31，不歸本票**：本票是 research／AFK 票，而 #13 的 Out of scope 排除實作。`HANDOFF.md` §8.2 原本把它歸給 #14，已更正。
2. **`gpu_memory_utilization` 的可行下界。** vLLM 需要 KV cache 至少裝得下一條 `max_model_len` 的序列，否則拒絕啟動；VoxCPM2 的 `max_model_len` 是 4096，但它的 per-token KV 大小未查證（ASR 側的 56 KiB/token 是 VibeVoice 的值，不能套用）。**怎樣才算查證**：讀啟動 log 的 `Available KV cache memory` 與 `kv_cache_size_tokens`，或直接二分找出會被拒絕的門檻。
3. **`stream_format="sse"` 對 VoxCPM2 是否可用。** 產生器與路由非 model-gated，但無任何 VoxCPM2 的官方範例／測試／recipe。**怎樣才算查證**：對本機 server 送 `stream=true, stream_format="sse", response_format="pcm"`，確認收到 `event: speech.audio.delta` 序列與帶 usage 的 `speech.audio.done`。
4. **`/v1/audio/speech/stream`（WebSocket）對 VoxCPM2 是否可用，以及逐句風格能否在單一連線上切換。** 文件說 session config 是 sticky、「All REST API parameters are supported」，但該端點不幫你切句，且 VoxCPM2 未被驗證過。**怎樣才算查證**：開一條 WS，連續兩次 `input.text` + `input.done`，兩次文字帶不同的 `(...)` 前綴，確認兩段音訊的情緒不同且 `utterance_index` 遞增。
5. **端點在 reference 模式下的行內風格強度是否與原生等價。** 兩邊的文字通道在結構上相同（同樣 tokenize 後進 text token 串），但端點多了 `split_multichar_chinese` 的 CJK 拆字步驟，且風格是否生效是模型行為而非結構性質。**怎樣才算查證**：同一段參考音與文字，分別經原生 `generate()` 與端點 `input="(cheerful tone)..."`，聽兩段輸出的情緒差異是否一致。
6. **continuation 模式是否真的完全忽略風格前綴，還是會把 `(...)` 唸出來。** `text = prompt_text + target_text` 意味著括號字串進了模型，但它落在中段。「ignored」是文件用語，實際是「不生效」還是「被朗讀」未查證。**怎樣才算查證**：送 `ref_audio` + `ref_text` 且 `input` 帶 `(cheerful tone)` 前綴，聽輸出裡有沒有把括號內容唸出來。這關係到 BFF 要不要在 continuation 路徑上主動剝除前綴。
7. **逐句延遲與首包延遲。** recipe 的穩態 RTF ~0.12（4090，offline 重用 engine）與 online ~0.5（含 HTTP round-trip）都不是「短句逐句」的情境，而逐句合成的成本結構偏向固定開銷而非 RTF。**怎樣才算查證**：以 10 到 20 字的中文短句連續送 20 次，量 p50／p95 的端到端與首包時間。
8. **`async_chunk: false` 對首包延遲的代價。** VoxCPM2 的 deploy yaml 關掉了 async-chunk pipelining（該路徑曾有 issue #3090 的截尾 bug）。打開它能不能降首包、bug 是否確實修好，未查證。**怎樣才算查證**：確認採用版本包含 #3090 的修補後，以 `async_chunk: true` 重跑 §6.7 的量測並比對首包時間與尾段完整性。
9. **VoxCPM2 的 `voice_profile` 三種 `mode` 在音色相似度上的實際差異。** `reference`／`continuation`／`ref_continuation` 的取捨（相似度對風格可控性）只有官方定性描述，沒有數字。這與 `2026-07-24-voxcpm-evaluation.md` §9.2 的待驗項是同一件事。**怎樣才算查證**：同一段台灣參考音預運算三個 profile，同一段文字各合成一次，A／B 聽測。

---

## 7. 來源清單

### primary（原始碼，逐行讀取 `main` 分支）

vllm-project/vllm-omni：
- `vllm_omni/entrypoints/openai/protocol/audio.py`（`OpenAICreateSpeechRequest` 全欄位）
- `vllm_omni/entrypoints/openai/tts_adapters/voxcpm2.py`（`VoxCPM2Adapter.validate`／`.build`）
- `vllm_omni/entrypoints/openai/tts_adapters/base.py`（`ARTTSAdapter`、`max_new_tokens` 界）
- `vllm_omni/entrypoints/openai/serving_speech.py`（`_build_voxcpm2_prompt`、`_load_supported_speakers`、`_load_precomputed_speakers`、`warmup`、`_resolve_ref_audio`、`_validate_tts_request`、`_REF_AUDIO_MIN_DURATION`／`_REF_AUDIO_MAX_DURATION`、`_generate_audio_sse_events`）
- `vllm_omni/entrypoints/openai/serving_speech_stream.py`、`vllm_omni/entrypoints/openai/api_server.py`（路由註冊）
- `vllm_omni/model_executor/models/voxcpm2/voxcpm2_talker.py`（`build_voxcpm2_prompt`、`_encode_raw_audio`）
- `vllm_omni/deploy/voxcpm2.yaml`
- `tests/e2e/online_serving/test_voxcpm2_tts.py`、`tests/e2e/online_serving/test_voxcpm2_tts_expansion.py`
- `examples/online_serving/text_to_speech/voxcpm2/{openai_speech_client.py,precompute_custom_voice.py}`

OpenBMB/VoxCPM：
- `src/voxcpm/core.py`（`VoxCPM._generate` 完整簽章與 prompt cache 生命週期）
- `src/voxcpm/model/voxcpm2.py`（`build_prompt_cache`、`_generate_with_prompt_cache`、`sample_rate`）
- `src/voxcpm/cli.py`（`build_final_text`、`--control` 與 `--prompt-text` 互斥）
- `pyproject.toml`、`README.md`、git tree（`main`，recursive）

vllm-project/vllm：
- `vllm/config/cache.py`（`CacheConfig.gpu_memory_utilization`／`.kv_cache_memory_bytes` docstring）
- `vllm/v1/worker/utils.py`（`request_memory`）
- `vllm/v1/worker/gpu_worker.py`（`Worker.init_device`、`determine_available_memory`）

### primary（官方文件）

- https://github.com/vllm-project/vllm-omni/blob/main/docs/serving/speech_api.md
- https://github.com/vllm-project/vllm-omni/blob/main/docs/user_guide/examples/online_serving/text_to_speech.md
- https://github.com/vllm-project/vllm-omni/blob/main/recipes/OpenBMB/VoxCPM2.md （recipe 自述 `Maintainer: Community`，記憶體數字已過期，見 §4）
- https://github.com/vllm-project/vllm-omni/issues/2896 、 https://github.com/vllm-project/vllm-omni/pull/2894 、 https://github.com/vllm-project/vllm-omni/issues/3090
- https://huggingface.co/openbmb/VoxCPM2/raw/main/README.md （~8 GB VRAM、bfloat16、48kHz、RTF ~0.30／~0.13、三模式定義）
- https://voxcpm.readthedocs.io/en/latest/usage_guide.html （「When Hi-Fi mode is enabled, the control instruction is ignored」、`(...)` 語法、參考音 5–30 秒）
- GitHub API：`repos/OpenBMB/VoxCPM`、`repos/vllm-project/vllm-omni`、`repos/a710128/nanovllm-voxcpm`

### primary（本專案內部實測值）

- `HANDOFF.md` §2.2／§2.4／§8.2／§8.3（vLLM 33654 MiB、aligner 3620 MiB、GPU 0 總量 46068／45465 MiB、以及「TTS 裝不裝得下現在算不出來」的三個理由）
- `docs/adr/0001-decoupled-model-serving.md`、`docs/adr/0002-design-voice-pinned-on-create.md`、`docs/adr/0003-rest-consumer-contract.md`、`CONTEXT.md`、`docs/spec.md`、`bff/src/vibe_vox/adapters/{base.py,stub.py}`

### community

- `a710128/nanovllm-voxcpm`（第三方推論引擎，MIT，官方 README 推薦。其 FastAPI 部分自述為 demo 且未上 PyPI）
- `2026-07-24-voxcpm-evaluation.md` 附錄所列的兩則第三方文章，本文未再引用

### 未查證

見 §6，九項，皆已附「怎樣才算查證」。
