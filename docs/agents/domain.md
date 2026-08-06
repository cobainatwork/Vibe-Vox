# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root（領域詞彙表）。
- **`docs/adr/`** — 讀與你將要動到的區域相關的 ADR。

若這些檔案不存在，**靜默略過**，不要標記其缺失，也不要主動建議建立。`/domain-modeling`（經 `/grill-with-docs` 觸發）會在術語或決策實際被解析時 lazy 建立。

## File structure（single-context）

原始碼分散在三個部署單元的目錄下，領域文件則集中於根層——這是刻意的：`CONTEXT.md` 的詞彙跨 BFF、前端與 aligner 共用，拆開會讓同一個概念有三份定義。

```
/
├── CONTEXT.md              # 領域詞彙表
├── docs/adr/
│   ├── 0001-decoupled-model-serving.md      # superseded，取代者為 #19
│   ├── 0002-design-voice-pinned-on-create.md
│   ├── 0003-rest-consumer-contract.md
│   └── 0004-word-level-forced-alignment.md
├── docs/api/               # 消費端契約（asr.md 已實作、tts.md 逐項標示）
├── bff/src/vibe_vox/       # FastAPI BFF
├── frontend/src/           # React 管理平面
└── aligner/                # 字級強制對齊服務
```

## Use the glossary's vocabulary

輸出中提及領域概念時（issue 標題、重構提案、假設、測試名），一律使用 `CONTEXT.md` 定義的用語，不要漂移到 glossary 明列 `_Avoid_` 的同義詞。若需要的概念尚未在 glossary，這是訊號：不是你在發明專案不用的語言（重新考慮），就是有真實缺口（記給 `/domain-modeling`）。

## Flag ADR conflicts

若輸出與既有 ADR 衝突，明確標示而非默默覆蓋：

> _Contradicts ADR-0003（REST consumer contract）— 但值得重開，因為…_
