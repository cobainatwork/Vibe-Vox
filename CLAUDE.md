# CLAUDE.md — Vibe-Vox 專案指引

Vibe-Vox 是自架的 ASR/TTS 後端（VibeVoice-ASR + VoxCPM2），為 `D:\pro\AI_practise` 智能陪練平台的 ASR/TTS 供應端，另含供操作者設定與測試的管理平面。設計脈絡見 `CONTEXT.md`（領域詞彙）、`docs/spec.md`（規格）、`docs/adr/`（架構決策）。

## Agent skills

### Issue tracker

Issues 追蹤於 GitHub Issues（cobainatwork/Vibe-Vox），使用 `gh` CLI。See `docs/agents/issue-tracker.md`.

### Triage labels

沿用五個標準 triage 角色標籤（標籤字串等於角色名）。See `docs/agents/triage-labels.md`.

### Domain docs

Single-context：根層 `CONTEXT.md` + `docs/adr/`。See `docs/agents/domain.md`.

### Handoff

交接只寫 `.remember/remember.md`（走 `/remember`），那是唯一會被自動注入下一個 session 的。**不要重建根層的 `HANDOFF.md`**，它已於 2026-08-08 刪除——一份可變動又被別的文件按章節號引用的長文件必然產生死引用。長內容各有其位：契約 `docs/api/`、決策 `docs/adr/`、詞彙 `CONTEXT.md`、部署與診斷 `docs/deployment.md`、量測數字與未開工的工作進 GitHub Issues。
