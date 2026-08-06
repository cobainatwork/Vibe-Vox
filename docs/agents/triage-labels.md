# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

Edit the right-hand column to match whatever vocabulary you actually use.

## 目前的使用狀況

五個標籤都已建在 repo 上。實際用過的是 `ready-for-agent`（#43）與 `ready-for-human`（#32）；`needs-triage`／`needs-info`／`wontfix` 尚未用過——本 repo 的 issue 都是自己開的，而 `/triage` 只服務「不是你開的」issue。

## 另有五個 `wayfinder:*` 標籤

`wayfinder:map`／`task`／`prototype`／`research`／`grilling` 屬 `/wayfinder` 的票型，由 #13 那個 effort 建立。它們不是 triage 詞彙，列在此處只為讓人知道那不是遺留垃圾。
