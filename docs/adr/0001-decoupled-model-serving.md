# 解耦模型服務，各能力走不同傳輸

## Status

superseded

## Decision

模型不內嵌於應用後端，而是獨立服務：VibeVoice-ASR 由 vLLM 提供、Qwen3-TTS 由獨立服務程序提供，兩者共用單張 RTX 6000 Ada 48GB。FastAPI 為薄 BFF，只負責編排、持久化、上傳轉檔與前端，透過各模型端點取得推論結果。

## Considered Options

- **模型內嵌 FastAPI**：單一 process 用 transformers 與 qwen-tts 直接載入。最少部署物，但需自寫 GPU 佇列，且應用邏輯與模型生命週期耦合。
- **純用現成 OpenAI-Compatible server、不做自己的後端**：否決。OpenAI 標準 schema 無法承載 Hotwords/音色 CRUD、持久化與統一頁，且 TTS 的 clone/instruct 與 ASR 的語者分離皆不在標準 schema 內。

## Consequences

- 各能力的傳輸不一致，必須明確記錄：ASR 辨識走 vLLM 的 `/v1/chat/completions`（官方 `/v1/audio/transcriptions` 未實作且標準 schema 會弄丟 Speaker）；TTS 的 clone/design/instruct 走原生 `qwen-tts` API 或非標準擴充端點（`/v1/audio/speech` 標準欄位不足）。
- vLLM 與 TTS 進程共用同一張卡，必須壓低 vLLM 的 `gpu_memory_utilization`（約 0.55–0.6）替 TTS 留出 VRAM，否則 TTS OOM。
- 好處：不必自寫 7B 的 GPU 管理，模型服務可獨立重啟與調校。
