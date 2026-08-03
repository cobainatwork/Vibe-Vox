# CLAUDE.md — Vibe-Vox 專案指引

Vibe-Vox 是自架的 ASR/TTS 後端（VibeVoice-ASR + Qwen3-TTS），為 `D:\pro\AI_practise` 智能陪練平台的 ASR/TTS 供應端，另含供操作者設定與測試的管理平面。設計脈絡見 `CONTEXT.md`（領域詞彙）、`docs/spec.md`（規格）、`docs/adr/`（架構決策）。

## Agent skills

### Issue tracker

Issues 追蹤於 GitHub Issues（cobainatwork/Vibe-Vox），使用 `gh` CLI。See `docs/agents/issue-tracker.md`.

### Triage labels

沿用五個標準 triage 角色標籤（標籤字串等於角色名）。See `docs/agents/triage-labels.md`.

### Domain docs

Single-context：根層 `CONTEXT.md` + `docs/adr/`。See `docs/agents/domain.md`.
