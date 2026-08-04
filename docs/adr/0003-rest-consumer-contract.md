# 消費端採 REST 契約，ASR 由 WebSocket 改為 REST

## Status

accepted

## Decision

Vibe-Vox 對消費者 AI_practise（智能陪練平台）提供 REST 契約：ASR 為 `POST /api/asr/transcribe`（回合制批次，回 `{segments:[{Start,End,Speaker,Content}], ...}`）、TTS 為 `/api/tts/*`（OpenAI 相容 `/api/tts/speech`，含可選串流回應）、Hotwords 為 `/api/hotwords`。放棄現行生產系統的 `ws://host:8088/ws/asr` WebSocket ASR 傳輸。TTS 後端由 CosyVoice 換為 Qwen3-TTS，維持 `/api/tts/speech` 契約形狀。

## Considered Options

- **維持 WebSocket ASR（現行生產契約）**：AI_practise 不必改 provider，但 VibeVoice-ASR 是批次模型、辨識在收到 `EOF` 後才開始，WS 只是把批次辨識包在較複雜的通道裡，付出連線生命週期成本卻拿不到真串流（邊講邊出 partial）的效益。
- **REST（採用）**：與批次、回合制用途完全契合，實作簡單、易於測試、逾時、重試與觀測。

## Consequences

- 破壞現行消費端：AI_practise 需新增 REST 版 `AsrProvider`。其 `IAsrProvider` 為可插拔設計（設定切換、`IAsyncEnumerable` 語義可包單一 final 結果），成本低且局部。
- TTS 因契約形狀不變，主要為端點重指與 Qwen3-TTS 的 instruct 對應；`ITtsProvider` 已支援分塊串流。
- TTS 串流回應納入範圍（第一音塊就緒即播，降低對話感知延遲）。
- 若未來需要邊講邊出的即時 partial ASR（live caption／barge-in），須改用真正的串流 ASR 模型並重新評估傳輸層。
- 消費端形狀為約束性：Hotwords 對消費端維持 `{id, word}`；TTS wav 輸出須 24kHz／mono／16-bit 以供消費端剝頭成 PCM。
- **ASR 回應於 #28 擴充字級對齊欄位**（ADR-0004 的連動項，向後相容——既有欄位形狀與值不變）：`segments[]` 加 `aligned: bool` 與 `words: [{Text, Start, End}]`，根層加 `alignment: {audio_duration, speech_start, speech_end, aligned_duration}`。完整形狀見 `docs/api/asr.md` §4.4。

  三項對消費端有實質影響的性質：

  **`Segment.Start`／`End` 的語義隨 `aligned` 改變**——true 時為首字與末字的實際發音邊界，false 時退回模型自選的切點。混用會得到錯誤結果。這是本契約唯一「同一欄位有兩種語義」之處，故 `aligned` 必須顯式檢查。

  **不可以 `words` 為空代替判斷 `aligned`**。空陣列與「該段沒有字」語義不同。

  **對齊失效不產生錯誤碼**。對齊服務不可用或逾時仍回 HTTP 200 與完整逐字稿，全段標記 `aligned: false`——逐字稿有獨立價值，不因評分這項附加功能失效而一併不可得。消費端無需為此加錯誤處理分支，但須容忍 `words` 恆空的情形。
