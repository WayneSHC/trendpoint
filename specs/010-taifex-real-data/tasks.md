---
description: "Task list for 010 — 真實台指期資料源（TAIFEX + FinMind）"
---

# Tasks: 真實台指期資料源（TAIFEX 主源 + FinMind 交叉驗證）

**Input**: Design documents from `specs/010-taifex-real-data/`

**Prerequisites**: plan.md、spec.md、research.md（D1-D8）、data-model.md、contracts/、quickstart.md

**Tests**: 憲章 III 與 spec FR-011 要求測試 → TDD；**network 標記測試 CI 不跑**（本機驗收）。

**Organization**: Foundational 承載 config/表名/品質檢查放寬 + **真實樣本 fixtures**；
US2（拼接引擎）**先行**——US1（端到端）依賴之（例外於 story 獨立原則，如實聲明）；
US1（TAIFEX 端到端 + 消費端切換）；US3（FinMind + 交叉驗證）。
**零回歸橫向約束**：mock 路徑測試與全套 pytest 每 checkpoint 綠。

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [x] T001 [P] 基線確認：`pytest -q` 全綠（154）；`pytest.ini` 註冊 `network` marker 並於
  addopts 預設排除（`-m "not network"`），驗證既有測試數不變
- [x] T002 [P] 取得離線 fixtures（一次性網路）：TAIFEX `futDataDown` 一個月真實 Big5 CSV
  截樣 → `tests/fixtures/taifex_sample_big5.csv`；FinMind `TaiwanFuturesDaily` 同區間
  JSON 截樣 → `tests/fixtures/finmind_sample.json`（此後解析測試全離線）

---

## Phase 2: Foundational（阻塞性前置）

- [x] T003 `config/config.py`：`FuturesDataSourceConfig`（backfill_start="1998-07-21"、
  throttle_seconds=2.0、max_retries=3、verify_tolerance=0.0，驗證界線見 data-model）掛
  `DataConfig.futures_source`（預設齊全零改相容）；`config.yaml` 加 `data.futures_source`
  區塊（**TXF source 此階段暫不切**，切換點在 T012）
- [x] T004 `db_security.py`：`raw_table_name_for(instrument, timeframe)` →
  `fut_{clean_id}_raw_{tf}`（現行 regex 已容納、零 regex 改動）；equity → ValueError；
  附單元測試（併入 tests/test_table_naming.py）
- [x] T005 `data_ingestion.py`：`validate_data_contract` 對**期貨連續序列**放寬正價檢查為
  「有限數值」（raw 層與現貨不動；參數化或依旗標，D8）；既有測試全綠

**Checkpoint**: `pytest -q` 綠（行為零變）。

---

## Phase 3: User Story 2 — 連續月拼接正確性（Priority: P2，先行）🎯

**Goal**: 量最大月轉倉 + back-adjust 引擎——US1 的依賴（例外聲明：引擎為兩 adapter 共用原語）。

**Independent Test**: `pytest tests/test_rollover.py` 綠（全離線錨定例）。

- [x] T006 [P] [US2] `tests/test_rollover.py`（先寫先 FAIL）：手造 3 契約量序列錨定例——
  交叉日 k（k−1 日量判定）→ k 日起換月；單向不回切（量回落）；back-adjust 平移數字
  手算（差額 = 新舊契約 k−1 收盤差、累積平移、Δclose 逐日等於同契約真實變動）；
  截斷不變性（近月選擇序列）；首日初始化（當日量最大）
- [x] T007 [US2] `data_sources/rollover.py`：`select_front_month` / `compute_roll_events` /
  `build_continuous`（契約見 contracts；純函式、向量化、MPL-2.0 標頭）

**Checkpoint**: SC-002 達成。

---

## Phase 4: User Story 1 — TAIFEX 真實資料端到端（Priority: P1）

**Goal**: 主源 adapter + ingestion 分流 + 消費端切換（003 mock 前綴條款兌現）。

**Independent Test**: 離線測試綠 + V6 network 驗收（T017）。

- [x] T008 [P] [US1] `tests/test_taifex_source.py`（先寫先 FAIL）：fixture 解析（Big5、
  欄位正規化、一般時段過濾、週契約排除）；欄位破壞 → fail-fast ValueError；重試語意
  （mock HTTP：失敗 max_retries 次後 RuntimeError、成功前 sleep 節流可注入 0）；
  另附 `@pytest.mark.network` 小區間真實 e2e（CI 跳過）
- [x] T009 [US1] `data_sources/taifex_source.py`：`TaifexAdapter`（fetch_raw 逐月
  POST+Big5+節流+重試；fetch_latest OpenAPI JSON；fetch = fetch_raw 全區間→rollover
  三步→連續序列；timeframe 僅 daily）；自 registry 註冊 source_key="taifex"
- [x] T010 [US1] `run_ingestion.py` futures 真源分流：raw 表空 → 回填
  （backfill_start～今日）；非空 → 自最後日期補至今日；raw 以（date×contract）冪等寫入
  `raw_table_name_for`；每次更新後重建連續序列 → 放寬版品質契約 → 整表覆蓋
  `fut_TXF_daily`；mock/csv/yfinance 路徑逐字不變
- [x] T011 [P] [US1] `tests/test_real_data_integration.py`（先寫先 FAIL）：含負價之連續
  序列過品質契約（SC-007）；taifex 源 instrument 之監控訊息無 MOCK 前綴 + **監控取數
  走 DB 連續表＋fetch_latest（不呼叫重量 fetch()）**（離線 stub，SC-006 精修版）；
  DB 無資料 → 警告略過不回填；回測引擎直接消費連續序列跑通
- [x] T012 [US1] `config.yaml`：TXF source mock→taifex（**切換點**）+
  `monitor_signals.py` 期貨取數分流（analyze H1：taifex 源 → 讀 DB 連續表＋
  fetch_latest 當日；mock/csv 源仍走 adapter.fetch；訊息/去重/前綴邏輯零改）；
  全套 pytest 綠 + 既有測試不依賴 config TXF source 之確認

**Checkpoint**: SC-003/006/007（離線部分）+ SC-001/008 之離線支柱。

---

## Phase 5: User Story 3 — FinMind 交叉驗證（Priority: P3）

**Goal**: 驗證源 adapter + 交叉驗證器（哨兵、不阻塞）。

**Independent Test**: `pytest tests/test_finmind_source.py tests/test_verify_futures.py` 綠。

- [x] T013 [P] [US3] `tests/test_finmind_source.py`（先寫先 FAIL）：JSON 樣本解析
  （欄位正規化同 raw schema）；`FINMIND_TOKEN` 缺失 → MissingTokenError
- [x] T014 [P] [US3] `tests/test_verify_futures.py`（先寫先 FAIL）：一致樣本零告警；
  注入超差 → 告警列含兩源數值與 diff；token 缺失 → skipped=True 且記錄原因、不拋錯
- [x] T015 [US3] `data_sources/finmind_source.py`：`FinMindAdapter`（REST 直打免 SDK、
  token 環境變數、fetch_raw/fetch 同契約）；註冊 source_key="finmind"
- [x] T016 [US3] `verify_futures_data.py`：`cross_verify(start, end, tolerance)` +
  CLI + 報表（stdout 摘要 + `data/verify_futures_report.csv`）；`run_ingestion.py`
  增 `--verify` 選項呼叫之

**Checkpoint**: SC-004 達成。

---

## Phase 6: Polish & Cross-Cutting

- [x] T017 V6 真實驗收（**需網路，本機一次**）：`pytest -m network -q` 綠；
  `run_ingestion.py` 全歷史回填（~340 請求，冪等重跑一次驗證）；`run_backtest.py`
  TXF 真資料跑通；`--verify`（有 token 則跑、無則記 skipped）；結果如實記入本檔完成註記
  （SC-001/008 正式驗收；mock 基準不再適用 TXF 屬預期資料切換）
- [x] T018 [P] 更新 `CLAUDE.md`（010 狀態：TXF 真源、rollover 引擎、驗證器）
- [x] T019 最終 `pytest -q` 全綠（合併關卡，不含 network）；quickstart V1–V5 確認

---

## 完成註記（2026-07-18，T017/T019 驗收實錄）

**T017 V6 網路驗收（全部如實記錄）**：

1. `pytest -m network -q` 綠（1 passed）。過程中抓到**雙語表頭**問題：TAIFEX 同一
   `futDataDown` 端點隨 session 時而回中文、時而回英文表頭（英文收盤欄叫 `Last`）——
   parser 已改雙語映射（commit `4479cd5` 前後系列）。
2. 全歷史回填：`fut_TXF_raw_daily` **34,722 列（1998-07-21 ~ 2026-07-17）**，
   ~340 請求 × 2s 節流，一次成功。
3. 回填後連續序列被品質契約誤擋——back-adjust 累積位移使早期價格穿零
   （close 範圍 -5,312 ~ 48,633，2,259 根 ≤ 0），`pct_change` 相對跳動比失義
   （出現 inf/負比）。修正：`allow_nonpositive_prices=True`（僅期貨連續層）時跳過
   相對跳動檢查，值正確性由 raw 層正價檢查 + 雙源交叉驗證把關；先紅後綠
   （commit `a75cf06`）。修正後連續序列入庫：**`fut_TXF_daily` 6,947 根、
   轉倉事件 335 次**。
4. 冪等重跑：偵測既有 raw → 增量（自 max date+1）、raw 維持 34,722 列不變、
   連續層整表重建成功。
5. `run_backtest.py` TXF 真資料**跑通**（載入 6,947 根、引擎完整執行、匯出
   trades/equity CSV）。結果如實記錄：總報酬 -636.06%、觸發爆倉護欄。
   成因非引擎缺陷——back-adjust 使早年調整後價位遠低於當年真實價
   （1999-06-21 進場 sizing_price=98，當年真實 TX ~7,500）→ 008b 保證金 sizing
   以調整後名目價值計算 → 保證金低估數十倍 → 463 口 → 正常波動即爆倉，
   護欄如設計強制結清並終止。**已知限制**：sizing/期交稅的價格基準應為未調整價
   （PnL 每點增量不受影響）；屬 010 範圍外，已立後續任務（連續表攜帶未調整參考價欄）。
   → **已由 spec 011 解決（2026-07-18）**：連續表增 `unadj_open/high/low/close`
   四欄（平移前擷取），sizing 取訊號根 `unadj_close`、期交稅取成交根 `unadj_open`
   套滑價；訊號與每點損益沿用調整後序列。同資料重測：**−636.06% → +54.43%**、
   單次口數 **463 → 5**、不再爆倉；1999-06-21 sizing 基準 98 → 8,439。
   另修一項本記錄未察覺的方向性錯誤：1999-07-14 出場因調整後成交價為 −2.0 而
   算出**負稅額 −1.856**，修正後為 +98.988。詳見 `specs/011-unadjusted-sizing-price/`。
   mock 基準（46.74%/4 筆）不再適用屬預期資料切換；現貨 CLI 數字亦因本次 ingestion
   刷新 10 年滾動窗而移動（資料變動、非程式變更；程式對照以 pytest 固定資料為準）。
6. `--verify` 無 FINMIND_TOKEN：印「未執行：FINMIND_TOKEN 環境變數未設定」、
   exit 0（skipped 語意正確）。有 token 的交叉驗證留待使用者提供 token 後執行。

**T019**：`pytest -q` **182 passed, 1 deselected（network）** 全綠；
quickstart V1–V5 由離線測試覆蓋（V1 解析錨定、V2 rollover 手算錨定、V3 監控紀律、
V4 驗證器注入分歧、V5 負價契約）。

---

## Dependencies & Execution Order

- Setup → Foundational → **US2（引擎先行）** → US1 → US3 → Polish；
  US1 依賴 US2（rollover 為共用原語——例外於 story 獨立，如實聲明）；
  US3 依賴 US2（FinMind fetch 亦走 rollover）與 T004（raw 表名）
- T001 ∥ T002；T003 ∥ T004 ∥ T005；T008 ∥ T011；T013 ∥ T014；T018 與 T017 可平行

## Implementation Strategy

1. fixtures 先行（T002）——解析測試的離線根基。
2. 引擎（US2）錨定例全手算，是資料正確性的核心關卡。
3. 切換點（T012）獨立成任務——TXF 改真源前 adapter 必須已註冊（避免中間態壞 config）。
4. V6 網路驗收（T017）collect 於本機、CI 永不出網。
5. 每任務或邏輯群組 commit。

## Notes

- 消費端（backtester/monitor/UI）零修改（003 mock 前綴由 source≠mock 自動消失）
- MTX 暫留 mock；盤中/券商 API/^TWII 不在範圍
- FinMind token 僅環境變數；缺失時驗證跳過、ingestion 不受影響
