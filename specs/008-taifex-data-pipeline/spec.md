# Feature Specification: 台指期資料管線 + Instrument 資產類別抽象（純資料層）

**Feature Branch**: `008-taifex-data-pipeline`

**Created**: 2026-07-13

**Status**: Draft

**Input**: User description（摘要）: 現行程式碼無資產類別抽象——`ticker` 是裸字串，隱含「yfinance symbol → `stock_*` 表 → 現貨股票、賣出課稅、long-only」。要支援台指期（TXF/MTX）需在資料來源、表命名、成本、口數、做空五處打破此假設。本規格（008a）**只做資料層**：引入 `Instrument` 資產類別抽象與可插拔資料來源，讓期貨（與未來其他資產）以加法接入、現貨路徑位元不變。作為 008b（成本/口數）與 spec 003（做空）的前置 enabler。

## 這是三段拆解的第一段

台指期完整支援拆為依序三個規格：**008a 資料層（本規格）→ 008b 期貨成本/口數 → spec 003 做空交易邏輯**。本規格聚焦資料層；回測引擎除**一道最小 fail-fast 護欄**外不碰（`Instrument` 穿線與成本/sizing dispatch 順延到 008b）。

## Clarifications

### Session 2026-07-13

- Q: 台指期完整支援怎麼切 spec？ → A: 三段依序 008a 資料 / 008b 成本口數 / 003 做空。
- Q: 資料來源怎麼定？ → A: 先設抽象層、來源後補；現在用 Csv/Mock adapter 跑通端到端，不碰真 TAIFEX/券商。
- Q: Rollover 責任歸誰？ → A: 各 adapter 自理拼接、交付連續序列；框架保持資產類別無關。
- Q: 抽象層多深入 + 008a 範圍？ → A: 完整資料層重構（Instrument 取代裸字串），但**範圍限資料層**：引擎不碰、順延 008b；對 futures 回測 fail-fast。
- Q: 期貨回測 fail-fast 護欄放哪層？ → A: 入口 + 引擎雙保險——回測入口腳本層 + 引擎方法內一道「僅拒絕、不做成本 dispatch」的 asset_class 護欄。008a 因此對引擎有**最小觸碰**（僅護欄；成本/sizing 仍 008b）。

## User Scenarios & Testing *(mandatory)*

使用者為量化研究者/開發者。以下三個旅程各為獨立可測切片。

### User Story 1 - 以宣告式 config 接入新資產類別的資料（Priority: P1）

作為研究者，我要在 `config` 宣告一個台指期 instrument（指定資產類別與資料來源），系統就能經可插拔 adapter 完成 ingest→驗證→存→載，得到一條符合資料契約的連續 OHLCV——**不需改動任何回測引擎程式**。

**Why this priority**: 這是本規格的核心新能力，也是 008b/003 的地基。

**Independent Test**: 註冊一個 futures instrument（Csv/Mock adapter，資料含一段 rollover 跳空的真實怪癖），跑 ingest→驗證→存→載，斷言得到標準 OHLCV + datetime 索引且通過資料契約。

**Acceptance Scenarios**:

1. **Given** config 宣告一個 `asset_class=futures`、`source=mock` 的 instrument，**When** 執行資料匯入，**Then** 系統以對應 adapter 取得連續序列、通過驗證、以 futures 表命名存入 SQLite。
2. **Given** 已存入的 futures 資料，**When** 載入該 instrument，**Then** 回傳符合資料契約（欄位齊全、時序遞增、無負價）的 DataFrame。
3. **Given** 一個未知 `source`，**When** 匯入，**Then** 系統 fail-fast 並回報明確錯誤（不靜默略過）。

---

### User Story 2 - 既有現貨工作流位元不變（Priority: P2）

作為現有使用者，我要所有既有 stock ticker 的匯入、表命名、回測與 parity 行為在本規格後**完全不變**（零回歸）。

**Why this priority**: 完整資料層重構的爆炸半徑大；向後相容是硬保證。

**Independent Test**: 全套 `pytest` 綠（含 spec 004 parity）；既有 `stock_*` 表命名與代表標的 equity 回測數字與重構前逐位元相同。

**Acceptance Scenarios**:

1. **Given** `config` 內既有純字串 tickers（如 `2330.TW`），**When** 載入設定，**Then** 各自解析為 `asset_class=equity`、`source=yfinance` 的 Instrument，行為與重構前相同。
2. **Given** 既有 `stock_2330_TW_daily` 資料表，**When** 經新表命名 helper 導出表名，**Then** 得到相同的 `stock_*` 名稱（不需重抓資料）。
3. **Given** 代表標的的 equity 回測，**When** 重構後執行，**Then** 交易數/報酬/parity 與重構前一致。

---

### User Story 3 - 誤用期貨回測被擋（Priority: P3）

作為研究者，我在成本模型（008b）就緒前若不慎對期貨 instrument 呼叫回測，系統要**fail-fast**，而非用股票型成本默默產出無意義數字。

**Why this priority**: 防呆護欄；避免 008a 中間態的 footgun。

**Independent Test**: 對一個 futures instrument 呼叫回測入口，斷言拋出明確錯誤且不產生績效數字。

**Acceptance Scenarios**:

1. **Given** 一個 `asset_class=futures` 的 instrument，**When** 呼叫單標的或組合回測，**Then** 系統拋出「futures 回測需 008b 成本模型」之明確錯誤。
2. **Given** 一個 `asset_class=equity` 的 instrument，**When** 呼叫回測，**Then** 正常執行（不受護欄影響）。

---

### Edge Cases

- **未知 source / asset_class**：fail-fast，明確錯誤。
- **Rollover 跳空**：adapter 交付的連續序列含換月跳空；驗證的離群門檻須以資產類別校準，不得誤判為壞資料。
- **futures 表命名撞 regex**：新命名須通過（放寬後的）表名白名單 regex；舊 `stock_*` 仍須有效。
- **config 同時有純字串 tickers 與結構化 instruments 宣告**：兩者合併為單一 registry，識別碼不得衝突。
- **adapter fetch 失敗**：比照現行（略過並記錄警告），不得中斷整批。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 系統 MUST 定義 `Instrument` 抽象，承載 `id`、`asset_class`（equity｜futures）、`source`（adapter 鍵）、顯示名、表命名鍵，並經 schema 驗證。
- **FR-002**: 系統 MUST 由 config 宣告 instrument registry；純字串 ticker MUST 向後相容解析為 `equity`/`yfinance` 的 Instrument。
- **FR-003**: 系統 MUST 提供資料來源 adapter 介面 `fetch(instrument, timeframe) → 連續 OHLCV`，依 `source` 分派，且至少提供 yfinance（包裝現行）、csv、mock 三種 adapter。
- **FR-004**: adapter 交付的序列 MUST 符合既有資料契約（標準 OHLCV 欄位 + `datetime` 索引、時序遞增、無負價、量≥0），且維持因果（沿用 ffill-only、不得 bfill）。
- **FR-005**: 表命名 MUST 集中於單一 helper；既有 ~7 處散落的表名導出 MUST 改用之；equity MUST 維持 `stock_*` 命名，futures 用獨立命名空間；表名白名單 regex MUST 同時接受兩者。
- **FR-006**: 資料驗證的離群跳動門檻 MUST 可依資產類別校準；equity MUST 維持現值。
- **FR-007**: 系統 MUST 對 futures instrument 的回測**雙層 fail-fast**：(a) 回測入口腳本（`run_backtest` / `run_portfolio_backtest`）於 dispatch 前檢查；(b) 回測引擎方法內一道最小 `asset_class` 護欄（僅拒絕、不做成本/sizing dispatch）。兩層皆 MUST 回報明確錯誤，且 MUST NOT 以股票型成本產出績效數字。
- **FR-008**: registry 與新參數 MUST 集中於 config + schema 驗證，MUST NOT 硬編碼。
- **FR-009**: 本規格的資料層改動 MUST NOT 改變任何既有 equity 回測數字；合併前 `pytest` 全綠（含 parity）並附回歸對照。
- **FR-010**: 系統 MUST 有端到端測試涵蓋一個 futures mock instrument（含 rollover 跳空）之 ingest→驗證→存→載。

### Key Entities

- **Instrument**：資產識別與資料相關中繼資料（id、asset_class、source、顯示名、表命名鍵）。008b 再擴充點值/合約/成本。
- **Instrument Registry**：由 config 宣告、解析（含純字串向後相容）出 Instrument 集合。
- **DataSource Adapter**：`fetch(instrument, timeframe) → 連續 OHLCV` 的來源實作（yfinance/csv/mock），內含各自的 rollover/正規化。
- **Table Name（表命名鍵）**：由 Instrument + timeframe 導出的 SQLite 表名（equity `stock_*` / futures 獨立命名空間）。
- **Asset-class 驗證設定**：per-asset-class 的資料契約參數（如離群門檻）。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 既有 `stock_*` 表命名與代表標的 equity 回測結果重構前後逐位元相同；全套 `pytest`（含 spec 004 parity）綠。
- **SC-002**: 能註冊一個 futures mock instrument（含 rollover 跳空）並完成 ingest→驗證→存→載，得到符合資料契約的連續 OHLCV。
- **SC-003**: 表命名集中於單一 helper，既有 ~7 處散落呼叫點皆改用之；表名 regex 同時接受舊 `stock_*` 與新 futures 命名。
- **SC-004**: 對 futures instrument 呼叫回測會 fail-fast（拋明確錯誤、零績效數字產出）。
- **SC-005**: 純字串 ticker 向後相容解析為 `equity`/`yfinance` Instrument（既有 config 不需改即可載入）。

## Assumptions

- **SQLite 整表覆蓋沿用**：現行 ingest 為整表覆蓋；對 back-adjust 連續序列而言正確（換月會回溯改動整段調整後歷史，本就需全量重算），故無需改為增量。
- **Rollover 內建於 adapter**：期貨連續序列由 adapter 交付；框架不建 rollover 引擎。
- **來源後補**：008a 只提供 yfinance（現行）+ csv/mock；真 TAIFEX/券商 adapter 日後另辦。
- **ffill-only 契約沿用**：`clean_kline_dataframe` 既有的向前填補、無 bfill、head dropna 語意不變（看前偏誤）。
- **建議命名**（`data_sources/` adapter 目錄、`table_name_for` helper、`fut_*` 命名空間、`data.instruments` config 鍵）為建議，最終命名於 `/speckit-plan` 定案。

## Out of Scope

- 期貨成本模型（期交稅、保證金、每口手續費、雙邊課稅）→ 008b。
- 口數 sizing（口 × 點值、保證金約束）→ 008b。
- 做空（`PositionManager` `direction=-1`、空方吊燈與進場）→ spec 003。
- 回測引擎（backtester / portfolio）穿 `Instrument` 與成本/sizing dispatch → 008b。（**例外**：008a 於引擎加一道最小 `asset_class` fail-fast 護欄，僅拒絕期貨、不做任何成本邏輯。）
- 真實 TAIFEX/券商 adapter（Shioaji 等）→ 日後。
- 框架內建 rollover 引擎 → 不做（adapter 自理）。
- 對期貨做實際可交易回測 → 待 008b + 003（008a 只驗資料進出）。
