# Issue tracker: GitHub

Issues 與 PRD 追蹤於本 repo 的 GitHub Issues（cobainatwork/Vibe-Vox）。所有操作使用 `gh` CLI，`gh` 於 clone 內自動推斷 repo。

## Conventions

- **建立 issue**：`gh issue create --title "..." --body "..."`（多行用 heredoc 或 `--body-file`）。
- **讀取 issue**：`gh issue view <number> --comments`。
- **列出 issue**：`gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'`，可加 `--label`／`--state`。
- **留言**：`gh issue comment <number> --body "..."`
- **標籤**：`gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **關閉**：`gh issue close <number> --comment "..."`

## Pull requests as a triage surface

**PRs as a request surface: no.** _(設為 `yes` 則本 repo 把外部 PR 視為功能請求；`/triage` 會讀此旗標。)_

## When a skill says "publish to the issue tracker"

建立一個 GitHub issue。

## When a skill says "fetch the relevant ticket"

執行 `gh issue view <number> --comments`。

## Blocking edges

優先用 GitHub 原生 issue dependencies：`gh api --method POST repos/cobainatwork/Vibe-Vox/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`（blocker 的數值 database id 由 `gh api repos/cobainatwork/Vibe-Vox/issues/<n> --jq .id` 取得，非 `#number`）。不可用時，退回在 body 或留言寫 `Blocked by: #<n>`。blocker 全部關閉即解除封鎖。
