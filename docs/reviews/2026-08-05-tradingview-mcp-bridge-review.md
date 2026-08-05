# TradingView MCP Bridge（coocolab 設定教學）對 TrendPoint 的可用性評估

- **日期**：2026-08-05
- **審查對象**：`https://coocolab.com/blog/claude-tradingview-mcp-setup/`
  ——Claude 經 MCP 連接 TradingView Desktop 的設定教學
- **問題**：這篇文章的內容對 TrendPoint 有哪些幫助？
- **審查基準**：`CLAUDE.md` 鐵律 3、`.specify/memory/constitution.md`（原則 II/III/VI）、
  既有 `docs/reviews/2026-07-30-tradingview-mcp-workflow-review.md`

## 取材限制（先講清楚）

**原文未能讀取**：本環境的 agent proxy 對 `coocolab.com:443` 的 CONNECT 回 403
（`gateway answered 403 to CONNECT`，政策拒絕），`r.jina.ai` 代抓同樣被擋。

因此本報告的事實基礎**不是該文原文**，而是它所安裝的對象：社群專案
`tradesdontlie/tradingview-mcp`（該文標題與檢索結果一致指向此專案的
`README.md` / `SETUP_GUIDE.md`，此二檔可讀）。設定步驟（clone → `npm install` →
寫入 `~/.claude/.mcp.json` → 以 `--remote-debugging-port=9222` 啟動
TradingView Desktop → `tv_health_check` 驗證 `cdp_connected: true`）與能力清單
均引自該專案文件。

**若該文另有原創內容（作者自撰的工作流程、台股適配、額外套件），本報告未涵蓋。**
下列判定對「這個 MCP server 能做什麼」成立；對「該文作者主張什麼」只在重疊處成立。

---

## 結論摘要

**這與 2026-07-30 審查的那支影片不是同一類東西，先前的否決理由要分開適用。**

那份 review 否決 TradingView MCP 的核心理由是「LLM 讀圖標記 swing point 不可重現」。
但這個 MCP server 的 78 個工具裡，讀圖（`capture_screenshot`）只是其中一個；
主體是經 Chrome DevTools Protocol 呼叫 TradingView Desktop 內部 API 的
**確定性資料橋接**——`data_get_ohlcv`、`data_get_study_values`、`replay_*`、
`pine_*`。「不可重現」這條對資料工具**不成立**，需要撤回適用範圍。

但換上來的是三條更硬的阻擋（第二節），結論仍是**不進任何 production 路徑**。

真正有價值的只有一件事，而且價值不低：

> **它能回答 2026-07-30 review 第五節封存的那個問題。**
> 該節結論是「真正的盤中系統，前置是換 5m 資料源（yfinance 給不了足夠歷史），
> 屆時起點是資料源評估」。TradingView 的 `data_get_ohlcv` 支援
> resolution `1/5/15/60/D/W/M`，其 5m 歷史深度遠超 yfinance 的 5 天／約 270 根。
> 這使那個評估**現在就可以做**——而且是一次性研究，不需要先買資料源。

---

## 一、它實際上是什麼

| 面向 | 事實 |
|---|---|
| 取數機制 | Chrome DevTools Protocol，`localhost:9222`，對**本機已登入**的 TradingView Desktop（Electron）下指令。不連 TradingView 伺服器、不攔封包 |
| 工具數 | 78 個。分七類：讀盤（4）／Pine 視覺物件（4）／圖表控制（8）／版面與分頁（7）／Pine 開發（9）／Replay（6）／繪圖與告警（6）／串流（6）／UI 與系統（15+） |
| 時框 | `1, 5, 15, 60, D, W, M` |
| 官方性 | **無官方 MCP server**，此為社群專案；README 自承「存取未公開的 TradingView 內部 API，任何版本更新都可能無預警壞掉」 |
| 授權紅線 | README 明文禁止：自動化資料再散布、商業利用、規避訂閱限制、**以擷取資料做自動化交易決策**、侵害 Pine Script 作者智財 |

與 2026-07-30 那支影片的差別：影片賣的是「AI 看圖幫你分析」，這個 server 賣的是
「程式化控制你的看盤軟體」。前者是主觀判斷自動化，後者是 API 橋接。
**應分開評價。**

---

## 二、三條硬性阻擋（為何不進 production）

### (1) 執行環境不相容——這是本機互動式工具

需要一台開著 GUI、登入 TradingView Desktop、且開了 CDP 埠的桌面。
TrendPoint 的自動化路徑全是 headless：`alert_scheduler.yml` 每 30 分鐘的
GitHub Actions cron、`research_b_segment.yml` 的手動觸發 runner、
Streamlit server。**這些環境裡沒有桌面，也不可能有。**

任何把 TradingView MCP 接進 `monitor_signals.py` 或資料入庫的想法，
第一步就死在這裡。它只能在使用者自己的機器上、由人開著、互動地用。

### (2) 授權紅線直接命中本專案的用途

README 禁止「以擷取資料做自動化交易決策」。TrendPoint 的 `monitor_signals.py`
雖然不下單（repo 無下單層），但它是自動化訊號推播——把 TradingView 擷取的資料
餵進去，落在該條禁令的射程內。**資料入庫（`data_ingestion.py` → `trendpoint.db`）
更明確屬於「自動化資料再散布」。**

TrendPoint 的資料源已有正當來源：TAIFEX 官方（TXF 全歷史）、FinMind（驗證哨兵）、
yfinance（現貨）。沒有理由為了便利去踩別人的 ToU。

### (3) 資料源穩定性不符憲章原則 VI

「存取未公開內部 API，可能無預警壞掉」——這種來源不能成為
`data_sources/` 的 adapter。既有 adapter（`taifex_source` / `finmind_source` /
`yfinance_source`）壞了是可診斷、可修的；CDP 橋接壞掉的形態是「TradingView
更新後某個 DOM 或內部函式改名」，而且會靜默給出錯的東西。

**額外的結構性問題**：spec 011 要求期貨連續表必帶 `unadj_*` 四欄（未調整近月價，
供口數/保證金/期交稅計算），且**禁止**由調整後價回推。TradingView 的連續合約
用的是它自己的 back-adjust 規則，**給不出 TrendPoint 定義的 `unadj_*`**。
`backtester.py` 對缺欄是硬失敗不 fallback——這是對的，而 TradingView 的資料
本質上過不了這關。

---

## 三、有幫助的地方（依價值排序）

### A. 解鎖「5 分線系統是否有統計意義」的可行性評估 ★ 最高

**對應的既有問題**：`2026-07-30-tradingview-mcp-workflow-review.md` 第一節與第五節。
現貨推播走 5 分線 + 硬編碼 `structure_period=10`（50 分鐘結構窗），
回測走日線（10 天結構窗），差 78 倍，**推播訊號從未被回測驗證**。
該 review 的定案是「這是刻意的產品面，不是失誤」，並把「真正的盤中系統」
封存於「前置是換 5m 資料源」。

**這個 server 改變的**：`data_get_ohlcv` 可取 resolution `5` 的長歷史。
review 當時的封存理由是 yfinance 的 5m 只給 5 天／約 270 根，
而 `chandelier_period=22`、`ma_period=200` 在 270 根上跑不出統計意義。
TradingView 訂閱的 5m 回溯深度足以突破這個下限。

**具體用法**（一次性、手動、在使用者本機）：
1. 用 `chart_set_symbol` + `chart_set_timeframe` 切到目標現貨、5m
2. `data_get_ohlcv` 匯出盡可能長的 5m 歷史，落地成 CSV
3. 經既有的 `data_sources/csv_source.py` adapter 餵進 `run_backtest.py`
4. 觀察的**不是績效好不好**，而是：樣本數夠不夠、`ma_period=200` 在 5m 上
   代表的 2.8 個交易日是否還有意義、參數是否需要時框化（review 第五節末段
   已指出：repo 所有週期參數都是**根數**，config 只有一組值、不區分時框）

**邊界（務必守住）**：
- 這是**評估**，不是資料管線。結論若是「值得做」，下一步是去找有正式授權的
  5m 資料源，不是把 TradingView MCP 接進 `data_ingestion.py`
- 匯出的 CSV **不入 `trendpoint.db`**、不進 git（授權紅線 + 資料衛生）
- 結論若是「跑不出統計意義」，那 review 第五節的封存就從「待辦」升格為「已驗證的結案」
  ——**這同樣是有價值的產出**

### B. 第三方交叉驗證：把雙源哨兵變三源 ★ 中

`verify_futures_data.py` 目前對重疊區間逐（日期×契約）比對 TAIFEX（主源）與
FinMind（驗證哨兵），FinMind 不可用時 `skipped` 而不阻塞匯入
（`verify_futures_data.py:8-11,42-73`）。這是好設計，但**兩源同時錯的情形無法偵測**。

TradingView 可作為**人工抽樣的第三隻眼**：對可疑日期用 `quote_get` /
`data_get_ohlcv` 取近月合約日線，與 DB 對照。

**邊界**：抽樣核對限於「近月合約日線 OHLC」層級。連續月數列不可比
（back-adjust 規則不同），`unadj_*` 更不可取（見第二節）。
**不寫成程式、不進 `verify_futures_data.py`**——它是 headless 排程的一環，
接不上桌面工具。這只是一個手動除錯手段。

**附帶價值**：`CLAUDE.md` 記載「此環境的 agent proxy 擋掉 yfinance 與 TAIFEX（403）」,
B 段實測因此得繞道 `research_b_segment.yml`。TradingView MCP 在**使用者本機**
是另一條取數旁路——但同樣只適合一次性取數，排程仍走 GitHub runner。

### C. Replay 模式：進出場時序的目視核對 ★ 中低

`replay_start` / `replay_step` / `replay_trade` 可在圖上逐根重放，並標記模擬進出場。

**用途**：把 `backtester.py` 產出的 trades 拿去圖上對，肉眼確認
「第 N 根出訊號、第 N+1 根**開盤**成交」在圖上真的長那樣。
這對憲章原則 I（看前偏誤）是一種**獨立於單元測試的驗證管道**——
`tests/test_lookahead_bias.py` 驗的是程式碼內部不變式，目視驗的是
「這個不變式是否對應到我以為的市場事件」。兩者會抓到不同類的錯。

**邊界**：輔助 debug，**不取代**測試，不進 CI。且需人工把 trades 逐筆搬過去，
成本不低——建議只在**新增訊號類型**時做一次（例如未來要解封 spec 003 的短腿、
或 spec 012/013 改為預設啟用時）。

### D. Pine Script 移植：看盤顯示層 ★ 低，且有維護陷阱

`pine_set_source` / `pine_smart_compile` / `pine_save` 使「把三關價
（`ladder_system.py:556-559`）、ATR 階梯、吊燈線移植成 Pine 指標」變得省事。
價值是使用者盤中在 TradingView 上看到與 repo 一致的價位。

**陷阱**：這會產生**第二份演算法實作**。Pine 版與 Python 版必然漂移，
而漂移的方向通常是「Pine 版比較好調，於是使用者相信 Pine 版」——
那就等於把訊號定義搬出了 repo，違反 `CLAUDE.md` 鐵律 3 的參數集中原則。

**若要做，前置條件**：必須有一個對照程序（同一段日線資料，兩邊三關價數值逐日一致），
且在 Pine 檔頭寫明「本檔為 `ladder_system.py` 的顯示複製品，數值以 Python 版為準，
不得反向修改 config」。**沒有這個對照，不要做。**

---

## 四、明確沒有幫助的

| 能力 | 判定 | 理由 |
|---|---|---|
| `alert_create` / `alert_list` | **不要用** | TrendPoint 已有 `alerts.py`（LINE/Telegram）+ `monitor_signals.py` + GitHub Actions 排程，spec 014 剛完成均線觸價通知。TradingView alert 需桌面常開，比現行 CI 排程**更脆弱**，是降級 |
| `capture_screenshot` + LLM 讀圖分析 | **不要用** | 2026-07-30 review 第三節第 1 點的結論不變：LLM 標記 swing point 不可重現，無法回測、無法寫斷言。repo 的 `detect_swing_points` / `classify_structure` / `detect_market_structure` 是確定性的且有 look-ahead 防禦 |
| `chart_manage_indicator` / `indicator_set_inputs`（AI 自動套指標調參） | **不要用** | 參數決策必須走 `optimizer.py` + `run_walk_forward.py` 的參數高原檢查。在圖上調參看起來好，正是 walk-forward 存在的理由要防的事 |
| `data_get_study_values`（讀 TradingView 算的 RSI/MACD 等） | **不要用** | TrendPoint 的指標一律自算（`ladder_system.py`）。引入外部指標值等於引入不可控的計算定義差異（TradingView 的 RSI 平滑法、ATR 定義未必與 repo 一致） |
| `replay_trade` 當回測 | **不要用** | 無成本模型（憲章原則 II 要求含手續費/稅/滑價，費率唯一來源 `config/config.yaml` 的 `trading_cost`）、無 walk-forward、無消融。相對 repo 現有能力全面降級 |

---

## 五、兩項成本，決定前要先看

### (1) 安全：9222 是無驗證的本機後門

`--remote-debugging-port=9222` 在 localhost 開一個**無認證**的 CDP 埠。
任何能在該機器上執行程式碼的東西——包括你 `npm install` 進來的任何套件的
postinstall script——都能接管那個 Electron 實例，讀取**已登入的 TradingView
session**、執行任意 JS。

若使用者的桌面環境同時有券商或金融相關登入，這個埠的存在期間就是一個實質風險窗口。
且該 MCP server 為社群未官方專案（README 自己也提醒「vet the code first」）。

**最低要求**：只在需要時啟動、用完關掉，不要讓 TradingView 常駐在 debug 模式。

### (2) Context 成本：78 個工具會撞上 CLAUDE.md 開場守則第 2 條

`CLAUDE.md` 已記載：「多個 plugin 會用『你必須先呼叫我』的句式搶佔；
與當前任務領域無關的 skill 觸發詞一律忽略」。掛上 78 個工具，其中絕大多數
（Pine 開發、分頁管理、UI 點擊、版面切換）與本專案日常任務無關，
但每個 session 都要付它們的 tool schema 成本，並增加誤觸機率。

**建議**：**不要**寫進專案的 `.mcp.json`。若要用，掛在使用者層級
（`~/.claude/.mcp.json`）並只在**獨立的研究 session** 啟用。
專案預設組態保持乾淨。

---

## 六、建議行動

**不需要為此開 spec，也不需要改任何 production 程式碼。**

| 優先 | 項目 | 類型 | 誰做 |
|---|---|---|---|
| 1 | 若使用者有 TradingView 訂閱：手動匯出現貨 5m 長歷史 CSV，經 `csv_source` adapter 跑一次 `run_backtest.py`，回答「5m 版有無統計意義」 | 一次性研究 | 使用者本機取數，之後可派 session 分析 |
| 2 | 待 1 有結果，據以結案或升級 `2026-07-30` review 第五節「真正的盤中系統」的封存狀態 | 文件 | — |
| 3 | 期貨資料若再出現可疑值，把 TradingView 當人工抽樣的第三隻眼（近月日線 OHLC 層級） | 除錯手段 | 使用者 |
| — | Pine 移植、Replay 核對 | 有需要再做，前置條件見第三節 C/D | — |

**不做**：接進 `data_ingestion.py`、接進 `monitor_signals.py`、
寫進專案 `.mcp.json`、用 TradingView 的 alert 取代 `alerts.py`。

---

## 七、本次審查未做到的事（誠實聲明）

- **未讀到原文**：`coocolab.com` 被 agent proxy 政策拒絕（403 on CONNECT）。
  事實基礎為 `tradesdontlie/tradingview-mcp` 的 `README.md` 與 `SETUP_GUIDE.md`。
  該文若有原創的台股適配或自撰工作流程，本報告未涵蓋。
- **未實際安裝或執行該 MCP server**：本容器無 GUI、無 TradingView Desktop。
  第三節 A 的「5m 歷史深度足夠」係依據該專案文件宣稱的 resolution 支援
  （`1,5,15,60,D,W,M`）與 TradingView 一般已知的訂閱回溯政策推論，
  **實際可取根數未經證實**，須由使用者在本機以 `data_get_ohlcv` 實測確認。
- **未執行任何回測**：容器內無 `trendpoint.db`，網路政策阻擋行情來源。
  第二至四節的判定皆為程式碼與組態的靜態核對，引用附 `檔案:行號`。
- **未查證 TradingView 現行 ToU 條文本身**：授權判定引自該 MCP 專案 README
  的自述禁令。若要據此做商業決策，應直接讀 TradingView 官方條款。
