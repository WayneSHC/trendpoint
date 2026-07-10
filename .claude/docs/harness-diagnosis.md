# Harness 診斷報告（2026-07-09，由 Fable 5 撰寫）

本檔是這套制度的「病歷」：目前環境最漏 token、最容易失焦、最容易出錯的前三名，
以及各自的具體修法。其他制度檔（model-dispatch、judgment-rubrics 等）的規則
都是針對這些病灶開的藥，改制度前先讀這份，才知道規則為什麼存在。

## 診斷方法

盤點於 2026-07-09 實測：讀取 `~/.claude/settings.json`、repo 結構、
session 系統提示中注入的 plugin/skill/MCP 清單。非臆測。

---

## 第 1 名：System prompt 膨脹 —— 不相關 plugin、skill 與未認證 MCP（最大 token 漏洞）

**症狀**：每個 session 開場就被注入 100+ 個 skill 描述與 50+ 個 deferred tool，
其中絕大多數與 TrendPoint（Python 量化交易研究工具）無關：
qdrant、cloudflare、vercel、carta-investors、finance 會計、design、
product-management、mcp-server-dev、agent-sdk-dev、frontend-design…。
另有 20+ 個「需要認證才能用」的 MCP server（slack、notion、figma、datadog…）
每次都佔用 context 卻永遠不可用。

**傷害**：
1. 固定 token 稅——還沒開始工作就先付掉數萬 token 的 context。
2. 失焦與誤觸發——多個 plugin 用「你必須（MUST）先呼叫本 skill」的強制句式互相搶佔，
   例如 superpowers 要求任何回應前先查 skill、carta 要求任何工具呼叫前先載入 carta skill。
   對 Sonnet 等級的模型，這種互相衝突的強制指令是實際的出錯來源。

**修法**（依序，前兩項可由模型代做但需使用者確認，見 maintenance-protocol.md）：
1. 裁剪 `~/.claude/settings.json` 的 `enabledPlugins`。對 TrendPoint 日常開發，建議保留：
   `code-review`、`commit-commands`、`claude-md-management`、`security-guidance`、
   `code-simplifier`；建議停用：`qdrant-skills`、`mcp-server-dev`、`agent-sdk-dev`、
   `frontend-design`、`understand-anything`（要用時再開）。`superpowers` 去留由使用者決定：
   它的 TDD/debugging skill 有價值，但它的強制觸發規則是失焦來源之一。
2. 在 claude.ai 的 connector 設定中，斷開未使用的 connector（slack、notion、figma、
   carta 等），只留實際會用的（如 obsidian、context7、github）。
3. 已在 CLAUDE.md 寫入路由規則：與本專案無關的 skill 觸發詞一律忽略（見 CLAUDE.md「開場必讀」第 2 點）。

**判準**：session 開場的 available-skills 清單若超過 ~30 條、或其中一半以上
與當前專案領域無關，就該回到本節重新裁剪。

---

## 第 2 名：指揮官下場 —— 主對話直接大量讀檔、掃 repo（最大失焦來源）

**症狀**：本 repo 之前沒有 CLAUDE.md，所以每個 session 都從 `ls` + 逐檔 Read 開始
重新認識專案。而本 repo 有多個大檔：`app.py`（49KB）、
`多空階梯優化與實戰策略.txt`（18KB×2）、`TrendPoint_OpenSpec.md`（11KB）。
主對話一次整檔 Read 就是數千 token，且會殘留整個 session，排擠後面真正的工作。

**傷害**：context 提前耗盡 → 被迫 compact → 遺失早期指令與驗收條件 → 後半段品質下降。
這是「session 越長越笨」的主因。

**修法**：
1. CLAUDE.md 已內建專案地圖與常用指令，開場不需要探索。
2. 遵守 `.claude/docs/model-dispatch.md` 的鐵律：大量讀取、掃 repo、查網頁、
   批次改檔一律派 subagent，主對話只收結論與 `檔案:行號`。
3. 讀大檔一律用 `Read` 的 offset/limit 讀目標區段，先用 Grep 定位再讀。

**判準**：主對話若已連續三次工具呼叫都是整檔 Read 或 ls 探索，就是違規訊號，
應改派一個 Explore agent。

---

## 第 3 名：Google Drive 同步路徑 + 中文路徑（最大出錯來源）

**症狀**：repo 位於
`~/Library/CloudStorage/GoogleDrive-.../我的雲端硬碟/TrendPoint`，
且 git worktree 也建在 Drive 資料夾內。

**傷害**：
1. 路徑含中文、空格與 `@`——未加引號的 shell 指令直接失敗或打到錯誤路徑。
   （`.claude/settings.local.json` 的歷史 allow 清單裡就有多筆為了繞過這問題的 perl 指令。）
2. Google Drive 會同步 `.git` 與 SQLite 檔（`trendpoint.db`）：同步延遲期間讀寫
   可能拿到半套檔案；多裝置同步可能產生 conflicted copy 損毀 git 物件庫。

**修法**：
1. 鐵律：Bash 中任何本 repo 路徑一律雙引號包住；能用 Read/Edit/Write/Grep/Glob
   工具就不要用 cat/sed/echo 重導向。
2. 生成物（`trendpoint.db`、`alerts.log`、回測 CSV）保持在 `.gitignore` 內（現況正確，維持）。
3. **建議但需使用者決定**：把 repo 移出 Google Drive（例如 `~/dev/TrendPoint`），
   或在 Drive 設定中將 `.git` 目錄排除同步。模型不得自行搬移 repo。

---

## 附註（未進前三，但已在制度中處理）

- **permissions allowlist 汙染**：`.claude/settings.local.json` 的 allow 清單累積了
  一次性的完整指令（含絕對路徑的 cat）。無害但無用，定期清理；
  通用型的（`git add *`、`python -m pytest -q`）值得保留。
- **記憶機制閒置**：`~/.claude/projects/...TrendPoint/memory/` 之前是空的。
  已建立 MEMORY.md 索引；踩坑教訓依 maintenance-protocol.md 寫回。
- **多個 plugin 的強制句式衝突**：優先序已在 CLAUDE.md 明定——
  使用者當下指示 > CLAUDE.md 與 `.claude/docs/` 制度 > plugin skill 的自我宣稱。
