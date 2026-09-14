# unhobble

[English](README.md)

一支 Claude Code skill，幫你的 `CLAUDE.md`、規則、skill 與 harness 設定瘦身，讓它們跟得上新一代模型。
先量測、重驗每個事實、一次提出刪減表，執行後雙向回讀驗證。

## 為什麼需要

為舊模型寫的指令不會一直有幫助。逐步指示、反覆叮嚀、同一條規則寫在三個地方——模型自己做得到之後，
這些東西會從「輔助」變成「綁手綁腳」（hobble），而且每一行都在每個 session 佔用 context。

靈感來自 Claude Code 的創造者 Boris Cherny 在 Y Combinator 的演講
[**〈We Cut 80% of Claude Code's Prompt〉**](https://www.youtube.com/watch?v=qyPCVqFUyDo)。標題就是核心：
Claude Code 團隊砍掉了自己大部分的 prompt。unhobble 把這件事變成可以在你自己的環境重複執行的流程。

> 本專案與 Anthropic、Boris Cherny 無任何關聯，也未獲其背書。

## 目前成效

在作者自己的 Claude Code 環境中，經過 unhobble 瘦身的 skill，多數執行時間**減少約一半**。
這是單一環境的個人觀察，不是對照實驗；你的數字會不一樣。

## 能做什麼

| 模式 | 對象 | 真正的問題 |
|---|---|---|
| `rules` | `CLAUDE.md`、`~/.claude/rules/` | 重複，**以及過期** |
| `skill` | skill 目錄 | 為舊模型寫的逐步鷹架 |
| `harness` | `settings.json`、plugin、MCP server、agent | 載入了卻沒在用；該寫成 hook 的規則（只提案，不改設定） |

每一刀都用同一個判準：**刪掉這段之後，在一個沒有既有程式碼可模仿的全新專案裡，模型的預設行為會不會不同？**
不會，就刪。

每一輪都走同一套骨架：

1. 先量測，再閱讀
2. 重驗每個事實（過期比冗長更傷：它永遠不會報錯）
3. 套用判準
4. 一次提案：刪減表的每一列都要回答「刪了之後由什麼保證？」
5. 一次確認後，整張表執行
6. 雙向回讀：該刪的不見了，**而且**該留的都還在
7. 用精確的 pathspec commit

提案表的每一列都標明動作：`delete`（刪除）、`merge`（合併）、`move-on-demand`（改為按需載入）、`fix-stale`（修正過期事實）、
`mechanize`（改由機制保證）。`mechanize` 會附一份可運作的 PreToolUse hook 草稿，由你決定是否安裝。

量測與事實檢查由附帶的腳本 [`skills/unhobble/scripts/measure.py`](skills/unhobble/scripts/measure.py) 執行（Python 3.9 以上，只用標準函式庫），腳本附自測，
skill 每次都先跑自測、通過才採信數字。skill 內也收錄了製作過程中實際踩過的坑（量測指令本身有 bug、節省估計系統性偏高、刪欄位留下斷掉的引用），
以及判斷檔案是否過大的門檻（[`thresholds.md`](skills/unhobble/thresholds.md)）。門檻是在單一環境量測的，
請依你的環境調整。

## 和其他做法比較

市面上已經有不少工具在瘦身或檢查 agent 指令。最接近的是 Claude Code 內建的 `/doctor`：它會刪掉 checked-in
`CLAUDE.md` 裡「Claude 能從程式碼推導出來」的內容、找出沒在用的 skill／MCP server／plugin，且改動前會先問。
unhobble 在這之上多做的：

| | unhobble |
|---|---|
| **範圍** | 同一個判準涵蓋三種對象：`CLAUDE.md`／rules、skill 目錄的**內容**、harness 設定 |
| **每一刀都要負責** | 每一列刪減都必須回答「刪了之後，這個行為由什麼保證？」答不出來的不算刪減 |
| **處理過期，不只處理冗長** | 判斷前先重測每個數字、路徑、計數；過期的規則比冗長的規則更傷 |
| **驗證的是改動本身，不只是結果** | 雙向回讀：該刪的逐項確認不見了，**而且**該留的逐項確認還在 |
| **skill 模式有否決權** | 改前改後都跑 skill 既有的測試或 evals，pass 數下降就不改（先確認檢查真的跑得起來） |
| **harness 模式永不改檔** | 用「常駐大小 × transcript 裡實際使用次數」排序 plugin、MCP server、agent，只提案 |
| **量測是經過測試的腳本** | 常駐位元組、按路徑觸發規則的單次載入量、memory 索引在哪裡被截斷、`@import`／路徑／指令是否存在，都由 `measure.py` 產出，不靠每次手打的指令；先跑自測 |
| **規則可以變成 hook** | `mechanize` 列會附一份 PreToolUse hook 草稿，並用一個違規、一個合規的輸入試跑過 |
| **把量測陷阱寫下來** | `paths:` 規則檔要按「一次載入多少」計、memory 索引有好幾道上限且單位不同（行數、位元組、字元）、harness 的警告不是上限；另附製作過程實際踩過的坑 |

### 這些情況用別的工具更合適

- **想用內建功能快速整理 `CLAUDE.md`、清掉沒用的擴充：** [`/doctor`](https://code.claude.com/docs/en/commands)。
- **要當 CI 關卡、或要確定性的事實檢查：** [agents-lint](https://github.com/giacomo/agents-lint) 會檢查引用的路徑是否存在、
  `npm run` 的 script 是否在 `package.json` 裡，並提供 CI exit code。unhobble 的重驗由模型執行：比較慢、要花 token，
  而且設計上只能由使用者手動啟動。
- **同時維護多種 agent 格式**（Cursor、Copilot、Windsurf、Gemini…）：[ctxlint](https://github.com/YawLabs/ctxlint)
  支援 16 種格式，並用 git 歷史判斷內容是否過期。unhobble 是圍繞 Claude Code 的檔案設計的
  （`CLAUDE.md`、rules、skill、`settings.json`）。
- **skill 觸發不準：** Anthropic 的
  [skill-creator](https://github.com/anthropics/claude-plugins-official/tree/main/plugins/skill-creator) 會用觸發 evals
  優化 description。unhobble 只砍內容，不調整觸發行為。
- **要補缺漏的指引，而不是刪減：**
  [claude-md-management](https://github.com/anthropics/claude-plugins-official/tree/main/plugins/claude-md-management)
  會對照程式碼稽核 `CLAUDE.md`，並把 session 裡學到的東西補回去。unhobble 做的是減法：刪除、把重複內容合併成一個指標、修正過期事實，
  但不會去找缺了什麼。
- **只處理 `CLAUDE.md`，把段落改寫成按需載入的指標：** [skill-claudemd](https://github.com/qiaeru/skill-claudemd)
  會驗證保留下來的每個指令、路徑、指標，並附 eval cases。
- **不存在檔案裡的內容**（工具輸出、檢索到的文字）：用執行期的 prompt 壓縮，例如
  [LLMLingua](https://github.com/microsoft/LLMLingua)。unhobble 是一次性編輯版控中的檔案，推論當下什麼都不做。

> 以上對其他工具的描述，依據的是 2026 年 9 月時它們的官方文件與 README。工具會更新，歡迎指正。

## 安裝

### 方式 A：plugin（建議）

在 Claude Code 中：

```
/plugin marketplace add HarveyJhuang1010/unhobble
/plugin install unhobble@unhobble
```

呼叫方式：`/unhobble:unhobble <對象>`

### 方式 B：直接複製 skill

```bash
git clone https://github.com/HarveyJhuang1010/unhobble.git
mkdir -p ~/.claude/skills
cp -R unhobble/skills/unhobble ~/.claude/skills/unhobble
```

呼叫方式：`/unhobble <對象>`

## 使用範例

以下範例用的是手動安裝的指令名；用 plugin 安裝的話，改打 `/unhobble:unhobble`。

```
/unhobble ~/.claude/CLAUDE.md
/unhobble rules
/unhobble skill ~/.claude/skills/my-skill
/unhobble harness
```

這支 skill 只能由使用者啟動（`disable-model-invocation: true`）：Claude 不會自己跑起來，
因為每一輪都以刪除文字收尾，必須由你確認刪減表。

## 授權

[MIT](LICENSE)
