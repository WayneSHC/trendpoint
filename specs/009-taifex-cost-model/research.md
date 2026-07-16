# Research: 台指期成本/口數模型（008b）

**Phase 0 產出** | 全部 NEEDS CLARIFICATION 已解（spec 零標記 + clarify 3 問已整合）。
本檔記錄設計決策與依據；D1–D5 於 brainstorming 定案、D6–D7 於 clarify 定案、D8–D9 為計畫期決策。

## D1：模型野心 — 完整槓桿模型

- **Decision**: 口數依保證金計、報酬以權益（return-on-margin）計，非固定 1 口、非 ATR 風險式。
- **Rationale**: 最貼近真實期貨操作；使用者（Wayne）明確選擇；槓桿風險顯性化是「誠實回測」的一部分。
- **Alternatives considered**: 固定 1 口 MVP（最快解護欄但把 sizing 債留給 003）；
  ATR 風險式 sizing（口數=資金×風險%÷(停損點數×乘數)，與現行階梯停損耦合較深、跨資產可比較好，
  但使用者選擇保證金式）。

## D2：保證金表示 — 名目值百分比

- **Decision**: 每口保證金 = 名目值（點數 × 乘數）× `margin_rate`（config，預設 0.055）。
- **Rationale**: 回測跨多年（指數 ~8000→~23000），固定 NT$/口 有時代錯置；百分比隨指數自動縮放，
  近似交易所依波動定保證金的行為；單一 config 數字。歷史原始保證金/名目約 4–6%。
- **Alternatives considered**: 固定 NT$/口（直覺但對早期低指數區間失真）；
  時變保證金排程表（最準但需維護歷史表 + 來源，工程最重，YAGNI）。

## D3：口數 sizing — 保證金使用率上限

- **Decision**: 口數 = floor(可用權益 × `margin_utilization` ÷ 每口保證金)，使用率預設 0.5。
- **Rationale**: 限槓桿、避免單根反向即重傷；單一 config 數字；all-in（使用率 100%）不實際。
- **Alternatives considered**: 全額 all-in（floor(權益÷每口保證金)，最大槓桿、追繳風險極高）；
  固定口數（不隨權益成長複利）。

## D4：引擎架構 — 可插拔 CostModel + PositionSizer + Contract（方法 C）

- **Decision**: 單一 `BacktestEngine`，依 asset_class 注入三個小元件；現股元件精確重現現況。
- **Rationale**: 鏡像 008a adapter 模式（已驗證成功）；現股 parity 最安全（現股元件 = 現行公式
  逐字搬移）；期貨/做空（003）是加法；元件為純函式最好測。
- **Alternatives considered**: 引擎內 `if asset_class` 分支（最快但現股/期貨纏繞、parity 風險高）；
  `FuturesBacktestEngine` 子類（隔離乾淨但逐根迴圈/出場邏輯複製一份，日後分歧難同步）。

## D5：權威費率 — TAIFEX 官方 + 券商加收預設 0

- **Decision**: 交易所每口每邊定額 = 經手費 + 結算費：TX 12+8=**20**、MTX 7.5+5=**12.5**、
  TMF 4.8+3.2=**8.0**（NT$）；期交稅 = 契約金額 × **0.00002**，兩邊各收；
  `broker_commission_per_lot` 預設 **0**（可調）；到期交割手續費**不計**（到期前平倉）。
- **Rationale**: 來源 = TAIFEX 官方「交易及結算相關費率」（https://www.taifex.com.tw/cht/4/feeSchedules ，
  2026-07 經瀏覽器渲染核實；注意經手費≠結算費，坊間常誤傳相等）。交易所費率是權威下限；
  券商實收另議，預設 0 = 可辯護下限、非零成本，使用者可填實際數字。
- **Alternatives considered**: 直接用坊間「大台來回 ~NT$70」聽說值（無權威來源，拒絕）；
  強制要求使用者提供券商數字才能跑（阻塞不必要）。

## D6：爆倉語意 — 當根強制結清並終止（clarify Q1）

- **Decision**: 權益 ≤ 0 當根：以當根價強制結清、權益曲線截止、summary 標記爆倉。
- **Rationale**: 真實帳戶歸零即結束；「停止開倉但持倉走完」會產生負權益持倉的復活曲線；
  終止語意最誠實、測試最好寫。
- **Alternatives considered**: 停止開新倉持倉走完（語意怪）；記警示續跑（違反誠實原則）。

## D7：整數口部分出場 — floor + 風控照做（clarify Q2）+ 不模擬追繳（clarify Q3）

- **Decision**: 部分平倉口數 = floor(持倉口數 × 比例)；0 口時跳過平倉但止損照移保本位。
  維持保證金追繳/強平不模擬（記為明示簡化）。
- **Rationale**: 平倉是獲利了結、移保本是風控，兩者獨立；鏡像現股 `round_to_lot(shares×0.5)`
  的既有取整先例（backtester.py:298）。追繳不模擬：階梯/吊燈止損 + 50% 使用率為實質防線，
  歷史維持保證金精確值難取得。
- **Alternatives considered**: 不足 1 口全平（改變策略語意）；不足 2 口停用階段 1（丟失保本風控）；
  模擬維持保證金強平（更真實但多一組難考證的參數，003 可再議）。

## D8：ContractSpec 放置 — Instrument 內生（計畫期決策）

- **Decision**: `ContractSpec`（乘數/tick/交易所定額費）定義於 `instruments.py`，
  作為 `Instrument.contract: ContractSpec | None`（現股 None）；帳戶/政策層參數
  （券商加收、稅率、滑價 tick、保證金率、使用率）進 `trading_cost.futures`。
- **Rationale**: 乘數與交易所費率隨**契約**變動（像 008a 的 source 一樣是 instrument 屬性）；
  保證金率/使用率是**使用者政策**。切在「契約內生 vs 帳戶政策」邊界最自然；
  兩處皆為 config SoT（instruments 也在 config.yaml 定義）。
- **Alternatives considered**: 全塞 `trading_cost.futures`（TX/MTX/TMF 各一組欄位，
  加契約要改 schema）；獨立 contracts.yaml（多一個 SoT 檔案，違反組態集中精神）。

## D9：引擎注入策略 — 參數預設 = 現股元件（計畫期決策）

- **Decision**: `run_backtest(...)` 新增可選元件參數（或 instrument 參數→內部工廠解析），
  **預設值 = 現股元件**；既有呼叫（含全部既有測試）零改動即走現股路徑。
  期貨由入口腳本顯式傳入。008a `assert_backtestable` 護欄改為放行 futures
  （函式保留、`FuturesBacktestNotSupportedError` 保留定義以免 import 斷裂，引擎不再呼叫拒絕）。
- **Rationale**: parity 最大保護——現股 code path 預設完全不變；期貨是顯式 opt-in；
  護欄退役採最小破壞（`test_futures_backtest_guard.py` 語意反轉改寫）。
- **Alternatives considered**: 強制所有呼叫端傳 instrument（觸碰面大、與 parity 目標衝突）；
  刪除護欄函式與例外類（斷 import、無必要）。
