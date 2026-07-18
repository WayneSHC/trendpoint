# Research: 真實台指期資料源（010）

**Phase 0 產出** | 零 NEEDS CLARIFICATION。D1–D2 於 brainstorming 定案（資料源實測
調查支撐）、D3 於 clarify 定案、D4–D8 為計畫期決策。

## D1：資料源 — TAIFEX 主 + FinMind 交叉驗證（使用者 Q1=C）

- **Decision**: TaifexAdapter 為主源（權威、免費、1998 起、實測可程式化）；
  FinMindAdapter 為驗證哨兵（同源清洗鏡像）。
- **Rationale**: 唯一權威來源 + 獨立傳輸路徑的雙重確認；yfinance 無台指期（實測排除）；
  券商 API 帳戶門檻高且日線 overkill。
- **Alternatives considered**: 單 TAIFEX（少一層保障）；FinMind 為主（第三方存續風險）。

## D2：連續月拼接 — 量最大月 + back-adjust（使用者 Q2=C）

- **Decision**: 次月日成交量 > 現行近月 → 次日起轉倉（單向不回切、每日判定）；
  轉倉日以新舊契約收盤差回溯平移全部歷史。
- **Rationale**: 資料內生、確定性可重現；back-adjust 保 Δ點正確（階梯/ATR/三關價/損益
  全吃相對量）；轉倉跳空若留存會產生假訊號（008a mock 的跳空即模擬此問題）。
- **Alternatives considered**: 固定結算日規則（需維護結算日曆）；不調整直接串接（假訊號）。

## D3：回填深度 — 全歷史 1998-07-21 起（clarify）

- **Decision**: `backfill_start` 預設 TX 上市日；config 可調。
- **Rationale**: 一次性 ~340 請求 ≈ 12 分鐘、幾 MB；涵蓋完整多空循環，研究價值最大。

## D4：FinMind 免 SDK——REST 直打（計畫期）

- **Decision**: 不安裝 FinMind Python 套件；requests 直打
  `https://api.finmindtrade.com/api/v4/data?dataset=TaiwanFuturesDaily&...`，
  token 走環境變數 `FINMIND_TOKEN`（無 token → 驗證跳過如實記錄）。
- **Rationale**: 驗證源只需一個 GET；少一個第三方依賴（憲章 VI 衛生）；
  token 絕不入 config 檔（安全鐵律）。
- **Alternatives considered**: FinMind SDK（多依賴、功能過剩）。

## D5：raw 表命名 — 零 regex 改動（計畫期）

- **Decision**: `fut_TXF_raw_daily`——經查現行 `TABLE_NAME_PATTERN` 中段
  `[a-zA-Z0-9_]+` 已容納底線，raw 表名天然合法；僅於 `db_security` 加
  `raw_table_name_for(instrument, tf)` helper（衍生自 `table_name_for`，中段加 `_raw`）。
- **Rationale**: spec FR-002 所預想的「regex 擴充」實際不需要——最小改動。

## D6：adapter fetch 契約分層（計畫期）

- **Decision**: `TaifexAdapter.fetch(instrument, tf)` 維持 008a 契約（回傳**連續序列**，
  消費端不感知拼接）；另提供 `fetch_raw(instrument, tf, start, end)` 供 ingestion 存
  raw 層與驗證器取數。回填/增量的決策（表空→回填、有資料→自最後日期補到今日）由
  `run_ingestion` 的 futures 分流負責，adapter 只管取數。
- **Rationale**: 008a 介面不變（mock/csv/yfinance 呼叫端零改）；raw 是 ingestion 的
  儲存決策不是 adapter 契約；增量邏輯集中一處。
- **Alternatives considered**: fetch 帶 mode 參數（介面污染）；adapter 直接寫 DB
  （越權——儲存屬 ingestion）。

## D7：交叉驗證定位 — 獨立腳本、不阻塞（計畫期，承 brainstorm）

- **Decision**: `verify_futures_data.py` 獨立 CLI（比對 raw 層：兩源同（日期×契約）之
  OHLC/結算/量，容差預設 0——同源鏡像理應全等；超差列印報表 + 存 `data/` CSV）；
  `run_ingestion --verify` 為便捷入口呼叫同一函式。FinMind 不可用（無 token/HTTP 錯）
  → 印明確「驗證未執行」訊息、退出碼 0（不阻塞）。
- **Rationale**: US3 獨立可測；哨兵定位（TAIFEX 為準）；容差 0 是同源鏡像的誠實預設
  （config 可放寬）。

## D8：正價檢查放寬範圍（計畫期）

- **Decision**: `validate_data_contract` 增參數（或依 asset_class="futures" 判定）：
  期貨連續序列之價格檢查由「>0」放寬為「有限數值（非 NaN/inf）」；現貨與期貨 **raw 層**
  檢查不動（raw 為真實市場價、必為正）。
- **Rationale**: back-adjust 平移使早期絕對價可能 ≤ 0（負價無害論證見 spec
  Assumptions）；放寬面最小化（僅連續層）。
- **Alternatives considered**: 平移加常數避免負價（污染絕對水位語意、且乘數會計不需要）。
