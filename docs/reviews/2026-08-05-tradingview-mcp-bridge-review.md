# TradingView MCP Bridge（Coocolab 繁中版）對 TrendPoint 的可用性評估

- **日期**：2026-08-05
- **審查對象**：`https://coocolab.com/blog/claude-tradingview-mcp-setup/`
  ——Claude 經 MCP 連接 TradingView Desktop 的設定教學
  → 其安裝對象為 `github.com/coocolab/Coocolab-Tradingview-MCP`
  （31 stars / 15 forks，JavaScript，MIT；為 `tradesdontlie/tradingview-mcp` 的繁中改編版）
- **問題**：這篇文章的內容對 TrendPoint 有哪些幫助？
- **審查基準**：`CLAUDE.md` 鐵律 2/3、`.specify/memory/constitution.md`（原則 II/III/VI）、
  既有 `docs/reviews/2026-07-30-tradingview-mcp-workflow-review.md`

## 取材限制（先講清楚）

**原文（部落格頁面）未能讀取**：本環境的 agent proxy 對 `coocolab.com:443` 的
CONNECT 回 403（`gateway answered 403 to CONNECT`，政策拒絕），`r.jina.ai`
代抓同樣被擋。

但**該文所安裝的 repo 可讀**，本報告的事實基礎為其
`README.md`、`SETUP_GUIDE.md`、`CLAUDE.md`、`RESEARCH.md`、`SECURITY.md`、
`rules.example.json`、`scalper-run.js`、`safety-check-log.json`。
設定步驟（clone → `npm install` → 平台腳本啟動 TradingView debug port →
寫入 `~/.claude/.mcp.json` → `tv_health_check` 驗證 `cdp_connected: true`）引自該 repo。

**若該文另有 repo 以外的原創內容（作者自撰的台股適配、額外工作流程），本報告未涵蓋。**

---

## 結論摘要

**這與 2026-07-30 審查的那支影片不是同一類東西，先前的否決理由要分開適用。**

那份 review 否決 TradingView MCP 的核心理由是「LLM 讀圖標記 swing point 不可重現」。
但這個 MCP server 的 78 個工具裡，讀圖（`capture_screenshot`）只是其中一個；
主體是經 Chrome DevTools Protocol 呼叫 TradingView Desktop 內部 API 的
**確定性資料橋接**。「不可重現」這條對資料工具**不成立**，適用範圍需縮回讀圖與
LLM 判讀類工具。

但換上來的是三條更硬的阻擋（第三節），**結論仍是不進任何 production 路徑**。

**真正有價值的有兩件事，而且其中一件與交易完全無關：**

> **1. 它能回答 2026-07-30 review 第五節封存的那個問題。**
> 該節結論是「真正的盤中系統，前置是換 5m 資料源（yfinance 給不了足夠歷史）」。
> `data_get_ohlcv` 支援 resolution `1/5/15/60/D/W/M`，5m 歷史深度遠超
> yfinance 的 5 天／約 270 根，使那個可行性評估**現在就能做**。
>
> **2. 它的 `RESEARCH.md` + `CLAUDE.md` 是一套可直接抄的 context 管理方法論。**
> 這與 TradingView 無關，是對 `CLAUDE.md` 鐵律 2 的實質補強——
> 該 repo 把「輸出規模預期表」寫進了 agent 守則，TrendPoint 目前只有行為規則、
> 沒有量化預期（詳見第四節 B）。

**同時查到一項必須先講的風險**：該 repo 根目錄有一支**無人工確認的自動下單機器人**
（`scalper-run.js`，BitGet 實盤），且附帶**真實成交日誌**——這與它自己 README
「禁止以擷取資料做自動化交易決策」的宣告直接矛盾（詳見第六節）。

---

## 一、它實際上是什麼

| 面向 | 事實 |
|---|---|
| 取數機制 | Chrome DevTools Protocol，`localhost:9222`，對**本機已登入**的 TradingView Desktop（Electron）下指令。不連 TradingView 伺服器、不攔封包 |
| 工具數 | 78 個：讀盤／Pine 視覺物件／圖表控制／版面與分頁／Pine 開發／Replay／繪圖與告警／串流／UI 與系統 |
| 時框 | `1, 5, 15, 60, D, W, M` |
| 市場 | 文件示例為美股（AAPL）、加密（BTCUSD）、期貨（ES1!、NYMEX:CL1!）。**無台股／台指期的支援說明** |
| 繁中版新增 | 晨間掃描（morning brief）workflow + `rules.json` 規則檔、`/skills`（chart-analysis、multi-symbol-scan、pine-develop、replay-practice、strategy-report）、`/agents` |
| 官方性 | **無官方 TradingView MCP server**，此為社群專案；README 自承「存取未公開的內部 API，任何版本更新都可能無預警壞掉」 |
| 授權 | 原始碼 MIT。README 另立使用禁令：自動化資料再散布、商業利用、規避訂閱限制、**以擷取資料做自動化交易決策** |

與 2026-07-30 那支影片的差別：影片賣的是「AI 看圖幫你分析」，這個 server 賣的是
「程式化控制你的看盤軟體」。前者是主觀判斷自動化，後者是 API 橋接。**應分開評價。**

---

## 二、`rules.json`：一個精確的反例

繁中版的核心新增是晨間掃描，其規則檔 `rules.example.json` 長這樣：

```json
"bias_criteria": {
  "bullish": ["價格在 20 EMA 和 50 EMA 之上", "RSI 在 50 以上且向上", ...]
},
"risk_rules": [
  "單筆交易最大虧損不超過帳戶 1%",
  "若當日已虧損 3%，停止交易",
  "重要財經資料發布前 30 分鐘不進新倉"
]
```

**這些是自然語言字串，交由 LLM 判讀。** 不是程式碼、沒有型別、無法測試、
不可重現——同一份資料跑兩次可能得到不同的 bias。

這正好是 TrendPoint 憲章原則 III/VI 與 `CLAUDE.md` 鐵律 3 要防的事。
特別值得對照的是 `risk_rules` 的第二條與 spec 013：

| | Coocolab | TrendPoint spec 013 |
|---|---|---|
| 「當日虧損 3% 停止交易」 | 一句自然語言，LLM 每天重新解讀 | `risk_gates.DrawdownGate` 狀態機 + 純函式，時序責任由呼叫順序契約承擔（迴圈開頭讀 `blocked`、尾端 `update()`） |
| 驗證 | 無 | 基準凍結於 `tests/fixtures/013_baseline_*`，關閉時逐筆逐根逐欄與實作前相同 |
| 是否敢宣稱有效 | 未討論 | **明文禁止**在 B 段實測（SC-014/015）完成前宣稱「降低了回撤」 |

**判定**：`rules.json` 對 TrendPoint 沒有可採納的內容，但它是一個好教材——
它示範了「把風控規則寫成人話交給 LLM」與「寫成有測試的狀態機」之間的差距。
spec 013 的做法是對的，這份對照可以佐證。

---

## 三、三條硬性阻擋（為何不進 production）

### (1) 執行環境不相容——這是本機互動式工具

需要一台開著 GUI、登入 TradingView Desktop、且開了 CDP 埠的桌面。
TrendPoint 的自動化路徑全是 headless：`alert_scheduler.yml` 每 30 分鐘的
GitHub Actions cron、`research_b_segment.yml` 的手動觸發 runner、Streamlit server。
**這些環境裡沒有桌面，也不可能有。**

任何把 TradingView MCP 接進 `monitor_signals.py` 或資料入庫的想法，第一步就死在這裡。

### (2) 授權紅線直接命中本專案的用途

README 禁止「以擷取資料做自動化交易決策」。`monitor_signals.py` 雖不下單
（repo 無下單層），但它是自動化訊號推播，落在該條禁令射程內。
資料入庫（`data_ingestion.py` → `trendpoint.db`）更明確屬於「自動化資料再散布」。

TrendPoint 已有正當資料源：TAIFEX 官方（TXF 全歷史）、FinMind（驗證哨兵）、
yfinance（現貨）。沒有理由為便利去踩別人的 ToU。

### (3) 資料源穩定性不符憲章原則 VI

「存取未公開內部 API，可能無預警壞掉」——這種來源不能成為 `data_sources/` 的 adapter。
既有 adapter 壞了是可診斷可修的；CDP 橋接壞掉的形態是「TradingView 更新後某個
內部函式改名」，而且**可能靜默給出錯的東西**。

**額外的結構性問題**：spec 011 要求期貨連續表必帶 `unadj_*` 四欄（未調整近月價，
供口數／保證金／期交稅計算），且**禁止**由調整後價回推。TradingView 的連續合約
用它自己的 back-adjust 規則，**給不出 TrendPoint 定義的 `unadj_*`**。
`backtester.py` 對缺欄硬失敗不 fallback——這是對的，而 TradingView 的資料
本質上過不了這關。

**另注**：該 repo 文件無任何台股／台指期支援說明，示例全是美股與加密。
TXF 在 TradingView 上的合約代碼與連續月規則需自行驗證。

---

## 四、有幫助的地方（依價值排序）

### A. 解鎖「5 分線系統是否有統計意義」的可行性評估 ★ 最高

**對應的既有問題**：`2026-07-30-tradingview-mcp-workflow-review.md` 第一節與第五節。
現貨推播走 5 分線 + 硬編碼 `structure_period=10`（50 分鐘結構窗），
回測走日線（10 天結構窗），差 78 倍，**推播訊號從未被回測驗證**。
該 review 定案「這是刻意的產品面，不是失誤」，並把「真正的盤中系統」封存於
「前置是換 5m 資料源」。

**這個 server 改變的**：封存理由是 yfinance 的 5m 只給 5 天／約 270 根，
而 `chandelier_period=22`、`ma_period=200` 在 270 根上跑不出統計意義。
TradingView 訂閱的 5m 回溯深度足以突破這個下限。

**具體用法**（一次性、手動、在使用者本機）：
1. `chart_set_symbol` + `chart_set_timeframe` 切到目標現貨、5m
2. `data_get_ohlcv` 匯出盡可能長的 5m 歷史，落地成 CSV
   （注意其硬上限為單次 500 根，需分批 + `chart_scroll_to_date` 翻頁）
3. 經既有的 `data_sources/csv_source.py` adapter 餵進 `run_backtest.py`
4. 觀察的**不是績效好不好**，而是：樣本數夠不夠、`ma_period=200` 在 5m 上
   代表的 2.8 個交易日是否還有意義、參數是否需要時框化

**邊界（務必守住）**：
- 這是**評估**，不是資料管線。結論若是「值得做」，下一步是找有正式授權的
  5m 資料源，不是把此工具接進 `data_ingestion.py`
- 匯出的 CSV **不入 `trendpoint.db`**、不進 git
- 結論若是「跑不出統計意義」，review 第五節的封存就從「待辦」升格為
  **已驗證的結案**——這同樣是有價值的產出

### B. Context 管理方法論 → 對 `CLAUDE.md` 鐵律 2 的直接補強 ★ 高（且與交易無關）

**這是整個 repo 對 TrendPoint 最實用、最能立刻落地的東西。**

其 `RESEARCH.md` 的三項發現中，第一與第二項直接推翻了一個常見假設：

> 1. **上下文管理是主要制約**——緊湊輸出設計將工作流從 **80KB+ 減至 5–10KB**
> 2. **工具數量未造成混淆**——「78 個工具看似過多，但配合清晰指示，
>    Claude 持續選擇正確工具」

**這修正了本報告初稿的一個判斷**：我原先把「78 個工具」列為 context 成本。
依該 repo 的實測經驗，成本不在工具數量，**在每次呼叫的輸出大小**。歸錯地方了。

其 `CLAUDE.md` 把這個發現制度化成幾樣 TrendPoint 沒有的東西：

| 他們的做法 | TrendPoint 現況 |
|---|---|
| **輸出規模預期表**：`quote_get` ~200 bytes、`data_get_ohlcv` 100 根 ~8 KB | 鐵律 2 點名 `app.py` 49KB、`docs/ladder-optimization-research.md` 為大檔，但**沒說各檔多大、讀一次要花多少** |
| **硬上限**：OHLCV 單次 500 根、Pine labels 每指標 50 個 | 無量化上限，只有「先 Grep 定位、再用 offset/limit 讀區段」的行為規則 |
| **預設緊湊**：`data_get_ohlcv` 預設 `summary: true`，`verbose` 需明確要求 | 無對應概念 |
| **決策樹**：依「你現在想知道什麼」路由到工具 | `CLAUDE.md` 開場守則第 1 條已有（按任務類型讀對應檔），**這條 TrendPoint 反而做得更好** |

**可採納的具體改動**（低成本，不影響任何交易邏輯）：
在 `CLAUDE.md` 鐵律 2 加一張**檔案規模表**——列出常被讀的大檔及其行數/KB，
標注建議讀法（整檔可讀／必須先 Grep／只讀特定區段）。目前鐵律 2 說了
「大檔先 Grep」，但沒說**哪些算大檔**，新 session 只能自己踩。

這件事與 TradingView 無關，也不需要安裝任何東西。

### C. 時框角色化 schema ★ 中

`rules.example.json` 的這三行值得單獨看：

```json
"timeframes": { "bias": "D", "entry": "4H", "management": "1H" }
```

它把時框**語意化成角色**（定方向／進場／管理），而不是散落的週期數字。

這正是 `2026-07-30` review 第五節末段那句話的具體形狀：

> 「repo 所有週期參數都是**根數**（`atr_period`、`ma_period=200`、
> `chandelier_period=22`、`time_limit=15`），config 只有一組值、不區分時框。
> 真要做多時框，第一步不是寫跨時框邏輯，而是**讓參數帶時框語意**。」

**若哪天真的做 MTF**，這個 schema 形狀可以參考——但必須進
`config/config.yaml` + Pydantic schema（鐵律 3 參數集中），
不是 JSON 自由文字。**現在不需要為它做任何事**，記下來即可。

### D. 第三方交叉驗證：把雙源哨兵變三源 ★ 中

`verify_futures_data.py` 目前對重疊區間逐（日期×契約）比對 TAIFEX（主源）與
FinMind（驗證哨兵），FinMind 不可用時 `skipped` 而不阻塞匯入
（`verify_futures_data.py:8-11,42-73`）。設計是好的，但**兩源同時錯無法偵測**。

TradingView 可作**人工抽樣的第三隻眼**：對可疑日期用 `quote_get` /
`data_get_ohlcv` 取近月合約日線，與 DB 對照。

**邊界**：限「近月合約日線 OHLC」層級。連續月數列不可比（back-adjust 規則不同），
`unadj_*` 更不可取。**不寫成程式、不進 `verify_futures_data.py`**——
它是 headless 排程的一環，接不上桌面工具。這只是手動除錯手段。

**附帶價值**：`CLAUDE.md` 記載「此環境的 agent proxy 擋掉 yfinance 與 TAIFEX（403）」，
B 段實測因此得繞道 `research_b_segment.yml`。此工具在**使用者本機**是另一條取數旁路
——但同樣只適合一次性取數，排程仍走 GitHub runner。

### E. Replay 模式：進出場時序的目視核對 ★ 中低

`replay_start` / `replay_step` / `replay_trade` 可在圖上逐根重放並標記模擬進出場。

**用途**：把 `backtester.py` 產出的 trades 拿去圖上對，肉眼確認
「第 N 根出訊號、第 N+1 根**開盤**成交」在圖上真的長那樣。這對憲章原則 I
是一種**獨立於單元測試的驗證管道**——`tests/test_lookahead_bias.py` 驗的是
程式碼內部不變式，目視驗的是「這個不變式是否對應到我以為的市場事件」。
兩者會抓到不同類的錯。

**邊界**：輔助 debug，**不取代**測試，不進 CI。需人工搬 trades，成本不低——
建議只在**新增訊號類型**時做一次（例如未來解封 spec 003 的短腿、
或 spec 012/013 改為預設啟用時）。

### F. Pine Script 移植：看盤顯示層 ★ 低，且有維護陷阱

`pine_set_source` / `pine_smart_compile` / `pine_save` 使「把三關價
（`ladder_system.py:556-559`）、ATR 階梯、吊燈線移植成 Pine 指標」變省事。
`RESEARCH.md` 也自陳「Pine 開發的編譯-錯誤-修復循環是本工具最強的應用」。

**陷阱**：這會產生**第二份演算法實作**。Pine 版與 Python 版必然漂移，
漂移方向通常是「Pine 版比較好調，於是使用者相信 Pine 版」——
那等於把訊號定義搬出了 repo，違反鐵律 3。

**若要做，前置條件**：必須有對照程序（同一段日線，兩邊三關價逐日數值一致），
且在 Pine 檔頭寫明「本檔為 `ladder_system.py` 的顯示複製品，數值以 Python 版為準，
不得反向修改 config」。**沒有這個對照，不要做。**

---

## 五、明確沒有幫助的

| 能力 | 判定 | 理由 |
|---|---|---|
| 晨間掃描 + `rules.json` | **不要用** | 見第二節。自然語言規則交 LLM 判讀，不可重現、無法回測 |
| `alert_create` / `alert_list` | **不要用** | TrendPoint 已有 `alerts.py`（LINE/Telegram）+ `monitor_signals.py` + GitHub Actions 排程，spec 014 剛完成均線觸價通知。TradingView alert 需桌面常開，比現行 CI 排程**更脆弱**，是降級 |
| `capture_screenshot` + LLM 讀圖分析 | **不要用** | 2026-07-30 review 第三節第 1 點的結論不變：LLM 標記 swing point 不可重現。repo 的 `detect_swing_points` / `classify_structure` / `detect_market_structure` 是確定性的且有 look-ahead 防禦 |
| `chart_manage_indicator` / `indicator_set_inputs`（AI 自動套指標調參） | **不要用** | 參數決策必須走 `optimizer.py` + `run_walk_forward.py` 的參數高原檢查。在圖上調參看起來好，正是 walk-forward 要防的事 |
| `data_get_study_values`（讀 TradingView 算的 RSI/MACD） | **不要用** | TrendPoint 指標一律自算（`ladder_system.py`）。引入外部指標值等於引入不可控的計算定義差異（TradingView 的 RSI 平滑法、ATR 定義未必與 repo 一致） |
| `replay_trade` 當回測 | **不要用** | 無成本模型（憲章原則 II 要求含手續費/稅/滑價，費率唯一來源 `config/config.yaml` 的 `trading_cost`）、無 walk-forward、無消融 |

---

## 六、安裝前必須知道的風險

### (1) repo 根目錄有一支無人工確認的自動下單機器人

`scalper-run.js` 是針對 BitGet 交易所 **XRP/USDT 的實盤自動交易程式**：

- HMAC-SHA256 簽章直連 BitGet 交易端點
- 迴圈 `for (let i = 1; i <= TOTAL_TRADES; i++)`，6 次週期、間隔 10 秒
- 取 30 根 1 分鐘 K 線，算 EMA(8)/RSI(3)/VWAP，
  `if (bullBias && rsi3 < 30) signal = "buy"` → **直接 `placeOrder("buy", size)`**
- **無任何人工確認環節**；另含 `placeSellWithRetry()` 自動繞過交易所的反洗盤鎖定

**這與同一個 repo 的自我宣告直接矛盾**：README 禁止「以擷取資料做自動化交易決策」，
`RESEARCH.md` 自陳「不適用於生產自動交易」。

### (2) 附帶真實成交日誌

`safety-check-log.json` 不是設定檔，是**實盤運行紀錄**：含真實 BitGet 訂單 ID
（如 `1425421555869495297`）、買入 7.0745 XRP 的成交、以及後續賣單因錯誤碼
12001 反覆失敗的序列。無 API 金鑰洩漏，但交易活動、持倉推斷與訂單 ID 可識別特定交易者。

**這對評估很重要，但不是道德批評，是工程判斷**：作者把自己的實盤日誌 commit 進了
公開 repo。你要授予這個 repo 的，是 `localhost:9222` 上**你已登入的 TradingView
session 的完全控制權**。一個對「什麼不該進版控」判斷有偏差的專案，值得多看幾眼再裝。

### (3) 9222 是無認證的本機後門，且該 repo 自己承認沒有存取控制

`SECURITY.md` 的防護只有「CDP 連線只在 `localhost:9222`，不對外暴露」，
**未提供任何存取控制、白名單或身分驗證**，其餘靠使用者的防火牆設定。

實際暴露面：任何能在該機器執行程式碼的東西——包括你 `npm install` 進來的
任何套件的 postinstall script——都能接管那個 Electron 實例、讀取已登入的
TradingView session、執行任意 JS。

**最低要求**：只在需要時啟動、用完關掉，不要讓 TradingView 常駐 debug 模式；
安裝前自行審 `src/` 的程式碼（README 自己也提醒 vet the code first）。

### (4) Context 成本（已修正）

本報告初稿把「78 個工具」列為主要 context 成本，**該判斷有誤**：
依 `RESEARCH.md` 的實測，工具數量不是制約，輸出大小才是（見第四節 B）。

但仍建議**不要**寫進專案的 `.mcp.json`——理由不是 context，而是
`CLAUDE.md` 開場守則第 2 條的 skill 觸發詞搶佔問題：該 repo 帶 `/skills`
（chart-analysis、multi-symbol-scan、strategy-report 等），這些名稱與 TrendPoint
的日常任務領域高度近似，會在無關情境被觸發。若要用，掛在使用者層級
（`~/.claude/.mcp.json`）並只在**獨立的研究 session** 啟用。

---

## 七、建議行動

**不需要為此開 spec。第四節 B 是唯一建議現在就做的改動，且與此工具無關。**

| 優先 | 項目 | 類型 | 誰做 |
|---|---|---|---|
| 1 | `CLAUDE.md` 鐵律 2 補一張**檔案規模表**（常讀大檔的行數/KB + 建議讀法） | 文件小改 | 可直接做，不需安裝任何東西 |
| 2 | 若使用者有 TradingView 訂閱：手動匯出現貨 5m 長歷史 CSV，經 `csv_source` 跑一次 `run_backtest.py`，回答「5m 版有無統計意義」 | 一次性研究 | 使用者本機取數，之後可派 session 分析 |
| 3 | 待 2 有結果，據以結案或升級 `2026-07-30` review 第五節「真正的盤中系統」的封存狀態 | 文件 | — |
| 4 | 期貨資料若再出現可疑值，把 TradingView 當人工抽樣的第三隻眼（近月日線 OHLC 層級） | 除錯手段 | 使用者 |
| — | 時框角色化 schema、Pine 移植、Replay 核對 | 有需要再做，前置條件見第四節 C/E/F | — |

**不做**：接進 `data_ingestion.py`、接進 `monitor_signals.py`、
寫進專案 `.mcp.json`、用 TradingView 的 alert 取代 `alerts.py`、
採用 `rules.json` 式的自然語言規則。

**安裝與否**：第六節的四項風險（尤其 1 與 2）建議先自行審 `src/` 再決定。
本報告不對「該不該裝」下判斷——那取決於使用者對該 repo 的信任評估。

---

## 八、本次審查未做到的事（誠實聲明）

- **未讀到部落格原文**：`coocolab.com` 被 agent proxy 政策拒絕（403 on CONNECT）。
  事實基礎為其 GitHub repo 的文件。該文若有 repo 以外的原創內容，本報告未涵蓋。
- **未實際安裝或執行該 MCP server**：本容器無 GUI、無 TradingView Desktop。
  第四節 A 的「5m 歷史深度足夠」係依據其文件宣稱的 resolution 支援與 TradingView
  一般已知的訂閱回溯政策**推論**，**實際可取根數未經證實**，須由使用者在本機
  以 `data_get_ohlcv` 實測確認。
- **未逐行審 `src/`**：第六節的風險判定係讀 `scalper-run.js`、`SECURITY.md`、
  `safety-check-log.json` 得出，**未審查 78 個工具的實作**。若要安裝，
  該審查仍須自行進行。
- **未執行任何回測**：容器內無 `trendpoint.db`，網路政策阻擋行情來源。
  第二、三、五節對 repo 的判定皆為程式碼與組態的靜態核對，引用附 `檔案:行號`。
- **未查證 TradingView 現行 ToU 條文本身**：授權判定引自該 MCP 專案 README
  的自述禁令。若要據此做商業決策，應直接讀 TradingView 官方條款。
- **未查證台股／台指期在 TradingView 上的資料可得性**：該 repo 文件無相關說明，
  示例全為美股與加密。TXF 的合約代碼、連續月規則、5m 回溯深度均須實測。
