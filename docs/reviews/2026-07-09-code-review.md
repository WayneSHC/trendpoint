# TrendPoint 開源前健檢與程式碼審查報告

- **日期**：2026-07-09
- **審查範圍**：ladder_system.py、backtester.py、portfolio_backtester.py、walk_forward.py、optimizer.py、performance.py、data_ingestion.py、monitor_signals.py、alerts.py、db_security.py、security_utils.py、config/、tests/、.github/workflows/、app.py（結構與安全掃描）、git 歷史敏感檔抽查、data/ 與文件資產
- **審查基準**：`.specify/memory/constitution.md` v1.0.0（第 I 條防看前偏誤為不可協商原則）

## Severity 統計

| 嚴重度 | 數量 |
|---|---|
| Critical | 1 |
| High | 4 |
| Medium | 6 |
| Low | 10 |
| **合計** | **21** |

| 類別 | 發現數 |
|---|---|
| 1. 正確性與交易邏輯（含 look-ahead） | 10 |
| 2. 安全與憑證外洩 | 4（token/.env 洩漏：**未發現**） |
| 3. 開源合規 | 3（內嵌第三方程式碼：**未發現**） |
| 4. Repo 衛生 | 4 |

---

## 第一類：正確性與交易邏輯風險（含 Look-Ahead Bias）

### 1.1 [Critical] portfolio_backtester.py:200 — 時間軸對齊使用 `.bfill()`，直接引入未來資料

> **✅ 已修復（2026-07-09，commit aff5d18，PR #3）**：改為 `_align_frames()` 僅 ffill、
> 進場迴圈加 NaN 防護、`tests/test_lookahead_bias.py` 新增兩個防禦測試（已驗證在舊實作
> 上會 FAIL）。前後回測對照見 PR #3 留言：總報酬 6.73%→6.45%、MDD -3.80%→-4.47%、
> 交易次數不變（差異全來自 inverse_vol 權重不再吃到回填的未來波動率）。

`aligned_dfs[ticker] = df.reindex(global_idx).ffill().bfill()`。多標的聯集時間軸中，較晚上市的標的（例如 00919 於 2022 年掛牌，00631L 於 2014 年）在其上市日之前的所有 K 線會被 `.bfill()` 用**上市後第一根 K 線的未來數值**回填——包括 close、指標欄、`mss_signal`、`bos_signal`、`regime_ok`、三關價等訊號欄位。回測迴圈（第 212 行起）會對這些「偽造的歷史」判斷進場並成交，違反憲法第 I 條。

**修法**：移除 `.bfill()`，改為 `df.reindex(global_idx).ffill()`，並在回測迴圈中以 `pd.isna(row['close'])` 或「該標的實際起始日」跳過尚未上市的 K 線；訊號欄位缺值一律視為無訊號（0 / False）。

### 1.2 [High] backtester.py:195-212、portfolio_backtester.py:319-351 — 訊號於第 N 根收盤判定、成交價也用第 N 根收盤，違反憲法 I 成交規則

憲法第 I 條明文：「訊號於第 N 根 K 線產生時，成交價一律使用第 N+1 根的開盤價（或明確標註的等效機制）」。實際實作：

- `check_entry_signal` 傳入的是**當根** `row['close']`、`row['open']`、`row['high']`、`row['low']`、`row['atr']`（backtester.py:195-207），momentum／trend／volatility／global 濾網全用當根資料；只有 structure 訊號用 `prev_row`（第 190-193 行）——同一次判斷混用兩種時基，邏輯不一致。
- 進場成交價為 `raw_price = row['close']`（backtester.py:211-212）；出場亦以 `row['close'] * (1 - slippage)` 成交（backtester.py:273、302）。

「當根收盤判定、當根收盤成交」不是嚴格的未來函數（`tests/test_lookahead_bias.py` 因此能通過——它只驗證 t 之後的篡改不影響 t 之前的交易），但在實務上不可執行：收盤價確定的那一刻已無法用該收盤價成交，屬於樂觀偏誤，且**與憲法條文直接牴觸**。目前程式中亦無「明確標註的等效機制」說明。

**修法**（二擇一，並更新憲法或程式使兩者一致）：(a) 將成交改為第 i+1 根 `open`（訊號判定維持第 i 根收盤資料）；或 (b) 在憲法與 README 明確標註採用「收盤判定、收盤成交＋滑價」等效機制，並統一 entry 各濾網時基（全部用已收盤的第 i 根，於第 i+1 根執行）。同時補一個「成交價必須來自訊號根之後」的 pytest。

### 1.3 [Medium] monitor_signals.py:128-133 — 即時監控以 `df.iloc[-1]`（可能尚未收盤的 K 線）判定訊號

盤中執行時，yfinance 回傳的最後一根 5 分鐘 K 線是**進行中**的 bar，其 close/high/low/volume 都會持續變動；以它判定 MSS/BOS/三關價突破會產生「repaint」訊號（推播後訊號消失），與第 137 行註解「訊號決策採用上一根已關閉 K 線」自相矛盾（實際取的是 `latest_idx = -1`）。

**修法**：判斷最後一根 K 線的時間戳；若 `now < bar_time + interval`（未收盤）則改用 `df.iloc[-2]` 作為「最新已收盤 K 線」。

### 1.4 [Medium] data_ingestion.py:84 — 資料清洗使用 `.ffill().bfill()`，bfill 屬未來值回填

`cleaned_df = cleaned_df.ffill().bfill()` 中的 `.bfill()` 會把序列開頭的缺值用未來資料回填。憲法第 VI 條規定「遇缺漏 K 線採向前填補並記錄警告」，並未允許向後填補；且此處填補時**沒有記錄任何警告**。

**修法**：改為 `ffill()`＋開頭殘留 NaN 直接 `dropna()`，並在有填補發生時 `print`/`logging.warning` 筆數。

### 1.5 [Medium] optimizer.py:52-115、117-144 — 全樣本網格尋優後將參數寫回 config.yaml（樣本內過擬合固化）

`optimize_ticker` 在**全部歷史**上尋優，`save_override_to_yaml` 把結果寫進 `config/config.yaml` 的 `ticker_overrides`，此後所有回測、儀表板 KPI 與即時訊號都用這組「看過全部答案」的參數，展示的績效實質上是樣本內成績。專案已有 walk_forward.py 正確處理此問題，但 optimizer 主流程未整合。

**修法**：在 optimizer 輸出與 README/UI 中明確標註「此為樣本內參數」；建議 `run_optimization.py` 預設改走 WalkForwardAnalyzer，或至少保留最後 20-30% 資料作為 hold-out 驗證後才寫回。

### 1.6 [Low] ladder_system.py:55-61 ＋ backtester.py:237 — ATR 前 period-1 根為 0，早期波動濾網失效且止損貼進場價

`calculate_atr` 對前 `period-1` 根回傳 0。後果：(1) `amplitude > 1.2 * atr`（ladder_system.py:390）在早期恆為 True，波動濾網形同停用；(2) `pm.stop_loss = execution_price - 2.0 * row['atr']`（backtester.py:237）在 ATR=0 時止損等於進場價，任何回檔立即止損。

**修法**：ATR 未成熟（前 period 根）回傳 NaN 並在進場條件中要求 `atr > 0`／`notna`。

### 1.7 [Low] ladder_system.py:419 — 時間止盈僅在 `stage == 1` 生效，減半後持倉無時間上限

`if bar_count >= time_limit and self.stage == 1`：一旦完成階段 1 減半（stage=2），剩餘部位只剩吊燈止損，`time_limit`「防禦時間維度風險」的設計對後半段部位不再成立。若為刻意設計（讓獲利部位奔跑），未見任何註解或規格佐證（**未確認**是否為預期行為）。

**修法**：在 spec（specs/006-exit-system-completion）明確此行為並補測試，或改為 stage 2 亦受時間上限約束。

### 1.8 [Low] ladder_system.py:210 ＋ backtester.py:256-257 — 吊燈止損被雙重移位（shift 兩根）

`calculate_chandelier_exit` 回傳前已 `.shift(1)`，回測迴圈又取 `prev_row['chandelier_long']`，實際使用的是 i-2 根的吊燈線。非偏誤（偏保守），但與兩處「防看前偏誤」註解語意不符，止損跟蹤比設計慢一根。

**修法**：擇一移位——建議函式內不 shift，由呼叫端統一取 `prev_row`。

### 1.9 [Low] ladder_system.py:413、443 — `manage_position` 回傳的 pnl_ratio 以止損價計算，與引擎實際成交價（收盤價）不一致

`realized_pnl = (self.stop_loss - self.entry_price) / self.entry_price`，但 backtester.py:302 實際以 `row['close'] * (1 - slippage)` 成交。所幸兩個引擎皆未使用該回傳值（僅用事件字串），屬死值＋語意陷阱；另注意止損是以「收盤價跌破」判定，盤中跌破不觸發，兩引擎行為一致但屬樂觀假設。

**修法**：移除 pnl_ratio 回傳或改回傳事件 enum；在文件標註「止損以收盤判定」的假設。

### 1.10 [Low] backtester.py:272、301 — 以中文事件字串精確比對驅動資金流

`if event == "階段 1 止盈 50% 成功，止損移至保本位"` 等硬字串比對橫跨 ladder_system.py 與兩個回測引擎，任何文案修改都會讓平倉分支靜默失效（部位管理器已平倉、引擎卻不賣股票，現金與淨值直接錯亂）。

**修法**：改用 Enum（如 `ExitEvent.STAGE1_HALF`、`ExitEvent.STOP_LOSS`）回傳，字串僅供顯示。

> 另註（非缺陷）：backtester.py:133 計算的 `temp_df['ladder']` 在回測迴圈中從未參與進出場判斷，僅 UI 展示用；作為「多空階梯系統」的回測引擎，階梯本身不在訊號路徑上，建議在文件中說明以免誤導使用者。

---

## 第二類：安全與憑證外洩風險

**憑證洩漏：未發現。** 全 repo（含 `git log --all --name-only` 歷史）從未追蹤過 `.env`；grep 掃描 *.py/*.yaml/*.yml/*.toml/*.json 未發現硬編碼 token、API key 或密碼；通知憑證一律經環境變數／GitHub Secrets 注入（alerts.py:47-58、alert_scheduler.yml:37-42），符合憲法安全條款。

### 2.1 [High] git 歷史（commit 8c5f5a6 起）— 曾追蹤 `range_navigator.db`（1.1MB SQLite）與 `alerts.log`，公開 repo 後仍可從歷史取出

`git log --stat` 顯示 `range_navigator.db`（後改名 `trendpoint.db`，於 4cedf88 移除）與 `alerts.log`（8c5f5a6 加入、4cedf88 與 0b27a3f 兩度移除）存在於 main 分支歷史。抽查 alerts.log 內容為訊號推播文字（無憑證）；.db 為二進位市場資料庫（內容**未確認**逐表檢查，但依寫入路徑應為 yfinance 市場資料與 sent_alerts 表）。雖非憑證，但：(1) 1.1MB 二進位垃圾永久膨脹歷史；(2) 其中的 Yahoo Finance 市場資料隨歷史一併公開（見 3.2）；(3) 舊專案名 range_navigator 殘留於歷史。

**修法**：公開前以 `git filter-repo --path range_navigator.db --path trendpoint.db --path alerts.log --invert-paths` 清史後 force-push；或更簡單——以目前 HEAD 內容開新的乾淨 repo（squash 全史）發布。

> **✅ 已修復（2026-07-10）**：以 `git filter-repo` 自全史移除 `range_navigator.db`、`trendpoint.db`、`alerts.log`、`Range_Navigator_OpenSpec.md` 與整個 `data/` 目錄後 force-push。改寫後經 `git rev-list --objects --all` 驗證歷史零殘留、HEAD 檔案樹除移除檔案外逐檔一致。**殘餘風險**：GitHub 伺服器端可能仍快取舊 commit（以舊 SHA 直連可及），徹底移除需聯絡 GitHub Support 執行 GC；另任何既有 clone/fork 仍保有舊史。

### 2.2 [Medium] optimizer.py:45 — f-string 拼接 SQL 表名，繞過 db_security 白名單

`pd.read_sql_query(f"SELECT * FROM {table_name}", conn)`，`table_name` 由 config tickers 組出，未經 `validate_table_name` 校驗。實際可利用性低（輸入來自本地 config），但違反憲法安全條款「SQLite 存取一律使用 db_security.py 之既有防護，禁止字串拼接 SQL」，且專案其他處（portfolio_backtester.py:72、app.py:355-356）都正確使用了白名單。

**修法**：`optimizer._load_data` 改呼叫 `db_security.safe_load_db_data(db_path, table_name)`。

### 2.3 [Low] alerts.py:106、122 — Telegram Bot token 內嵌於 URL，例外訊息可能將 token 印入日誌

`url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"`，連線失敗時 `print(f"Telegram 網路連線錯誤: {e}")`——requests 例外訊息通常包含完整 URL（含 token）。GitHub Actions 會遮罩已註冊 secrets，但本地執行的終端與任何轉存日誌不會。

**修法**：捕捉例外時只印 `type(e).__name__` 或以 `str(e).replace(self.tg_token, '***')` 遮罩。

### 2.4 [Low] app.py:59-61 ＋ security_utils.py:57-77 — 密碼閘門未設定即整站放行；鎖定機制以 session_state 為界可被重開 session 重置

`if "password" not in st.secrets: return True` 表示部署時忘記設定 secrets 就是無密碼公開站（僅有註解提醒）；登入鎖定計數存在 `st.session_state`，攻擊者重整頁面／開新 session 即歸零，防暴力破解效果有限；另 `==` 比較非常數時間（Streamlit 場景下風險極低）。

**修法**：部署文件明確要求設定 password；鎖定計數改以來源 IP＋跨 session 儲存（如 SQLite）為鍵；比較改 `hmac.compare_digest`。

> 另註：alert_scheduler.yml:6 `cron: '*/30 * * * *'` 全年無休每 30 分鐘執行（含收盤與週末），對公開 repo 是無意義的 Actions 配額消耗，建議限縮至台股交易時段（週一至五 01:00-06:00 UTC）。

---

## 第三類：開源合規風險

**內嵌第三方程式碼：未發現。** 所有模組皆為自寫實作（無 vendored 套件、無抄錄的指標庫程式碼）；相依套件（pandas/numpy/yfinance/streamlit/plotly/pydantic/numba 等）皆為 pip 宣告、授權相容。app.py:92 以 CSS `@import` 載入 Google Fonts（IBM Plex，OFL 授權），僅連結未內嵌，無虞。

### 3.1 [High] 專案根目錄 — 無 LICENSE 檔案

repo 中沒有任何授權條款（`README.md` 亦未宣告）。沒有 LICENSE 的公開 repo 在法律上「保留所有權利」，他人無權使用、修改或散布——形式上公開了，實質上並未開源。

**修法**：加入明確的 OSI 授權（MIT／Apache-2.0 為常見選擇；Apache-2.0 額外含專利授權條款），並在 README 標註。同時建議在 README/LICENSE 附「非投資建議」免責聲明（交易系統開源的慣例防護）。

### 3.2 [High] data/*.csv（10 檔，共 10,853 列）— 公開再散布 yfinance 抓取之 Yahoo Finance 市場資料有 ToS 疑慮

`data/0050_TW_daily.csv` 等 10 檔為 yfinance 下載的 Yahoo Finance OHLCV（daily 最長回溯至 2016 年、約 2,400 列/檔）。Yahoo 服務條款一貫禁止對其資料的再散布／商業利用，yfinance 官方文件也明示「僅供個人研究用途、不得再散布」（法律結論**未確認**——各法域對事實性市場資料的著作權保護不一，台股原始價量另涉 TWSE 資訊使用條款，但 ToS 違約風險與平台下架風險是實際存在的）。git 歷史中的 SQLite 資料庫（見 2.1）含同類資料，需一併處理。

**修法**：將 `data/*.csv` 自 repo 移除並加入 `.gitignore`（`data/*.csv`），README 引導使用者以 `python run_ingestion.py` 自行抓取；若測試需要固定資料，改用程式生成的合成 fixture（tests/test_lookahead_bias.py 已示範此作法）。

> **✅ 已修復（2026-07-10）**：`data/` 全目錄（含所有 CSV）已自 HEAD 與全部歷史移除（與 2.1 同一次 filter-repo），`.gitignore` 加入 `data/`。測試與 CI 經 grep 確認不依賴 data/ 檔案。本地快取檔不受影響，`run_ingestion.py` 可隨時重建。

### 3.3 [Medium] 多空階梯優化與實戰策略.txt／_extracted.txt、extracted_images/*.png（9 張）、三 bands 文件 — 內容來源與著作權未確認，不宜直接公開

`extract_math.py`/`map_images.py` 顯示這兩份 txt 與 extracted_images/ 是從一份不在 repo 內的 `多空階梯優化與實戰策略.docx` 抽取的文字與圖片。該文件為「深度優化研究報告」體裁，來源**未確認**（自有創作、AI 研究工具產出或第三方文獻皆有可能）；其中圖片若引用自第三方圖表／書籍截圖，公開即構成侵權散布。`three_bands_theory.md` 為對「台指期三關價」（源自台灣期貨交易社群的第三方交易理論）的整理，文字若為自行撰寫則風險低。`產品需求文件 (PRD).txt` 為內部產品文件，公開與否屬意願問題。

**修法**：逐一確認著作權歸屬——確定自有者保留並在檔頭標註作者與授權；無法確認者（尤其 extracted_images/*.png 與兩份 txt）在公開前移出 repo（連同 git 歷史）。內部文件建議移至 `docs/` 或移除。

---

## 第四類：Repo 衛生

### 4.1 [High]（與 2.1 同源）git 歷史需清理後才適合公開

歷史含 1.1MB 二進位資料庫、alerts.log、舊專案名 `range_navigator.db`。目前追蹤中的檔案樹經 grep 確認**已無** Range Navigator 命名殘留（僅未追蹤的 `.claude/settings.local.json` 留有改名指令記錄，該檔未入版控，無虞）。修法見 2.1。

> **✅ 已修復（2026-07-10）**：見 2.1——歷史已清理並 force-push，舊專案名檔案（`range_navigator.db`、`Range_Navigator_OpenSpec.md`）已一併自歷史移除。

### 4.2 [Medium] data/*_5m.csv — 追蹤了僅 5 天滾動窗的即時性資料，屬會腐化的可再生產物

5 分鐘線 CSV（每檔約 244 列）來自 `period="5d"` 抓取，入庫當下就開始過期，對任何 clone 者都是「舊的 5 天」。憲法第 VI 條要求「版本庫只追蹤原始輸入與程式碼／規格」，這些檔案是 `run_ingestion.py` 一鍵可再生成的產物（daily CSV 同理，且與 3.2 的 ToS 問題重疊）。

**修法**：與 3.2 一併移除所有 `data/*.csv`，`.gitignore` 加入 `data/*.csv`。

> **✅ 已修復（2026-07-10）**：見 3.2。

### 4.3 [Low] extract_math.py:31-35、map_images.py:34-37 — 一次性 docx 抽取腳本引用 repo 中不存在的檔案

兩支腳本的 `__main__` 都指向 `多空階梯優化與實戰策略.docx`（未入版控），對開源使用者是無法執行的死程式碼，且會引導讀者注意到 3.3 的來源問題。

**修法**：連同其產物（兩份 txt、extracted_images/）一併移除；若想保留工具價值，改為接受 CLI 參數的通用腳本並移至 `tools/`。

### 4.4 [Low] 根目錄文件雜訊 — `產品需求文件 (PRD).txt` 檔名含空格與全形括號、TrendPoint_OpenSpec.md 自述為「歷史文件」

檔名含空格／中文括號對跨平台腳本與部分 CI 工具不友善；根目錄堆放歷史文件降低開源第一印象的專業度。

**修法**：建立 `docs/` 收納（如 `docs/prd.md`、`docs/openspec.md`），檔名改 ASCII kebab/snake case。

> 正面觀察（值得保留的優點）：`.gitignore` 已正確涵蓋 `.env`、`*.db`、`*.log` 與回測產物；tests/ 共 41 個測試，含專門的 look-ahead 篡改測試（test_lookahead_bias.py）、SQL 注入白名單測試（test_security.py）與 rate-limiter/lockout 測試；CI 同時驗證 Numba 降級路徑（tests.yml:37-40），與憲法第 IV 條對齊。

---

## 開源前行動清單（建議順序）

1. **決定授權**：加入 LICENSE（3.1）＋免責聲明。
2. **移除資料資產**：刪除 `data/*.csv`、兩份策略 txt、extracted_images/、extract_math.py、map_images.py（3.2、3.3、4.2、4.3）。
3. **清理 git 歷史**：filter-repo 或開新 repo squash 發布（2.1、4.1）。
4. **修 Critical**：portfolio_backtester.py 的 `.bfill()`（1.1），並補一個「晚上市標的不得在掛牌前產生交易」的測試。
5. **對齊憲法 I**：決定成交機制（N+1 開盤 vs 標註的收盤成交等效機制）並統一實作與文件（1.2）。
6. 其餘 Medium/Low 依序處理（1.3-1.5、2.2-2.4）。
