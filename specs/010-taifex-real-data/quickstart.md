# Quickstart: 真實台指期資料源（010）驗證指南

**Phase 1 產出** | V1–V6 對映 SC-001~008。

## 前置

```bash
.venv/bin/python -m pytest -q      # 基線 154 綠（003 完成時）
```

## V1 — 拼接引擎（SC-002，離線）

```bash
.venv/bin/python -m pytest tests/test_rollover.py -q
```

錨定例：量交叉次日轉倉、單向不回切、back-adjust 後 Δclose 逐日等於同契約真實變動、
截斷不變性（近月選擇序列）。

## V2 — 解析層（SC-003，離線 fixture）

```bash
.venv/bin/python -m pytest tests/test_taifex_source.py tests/test_finmind_source.py -q
```

真實 Big5 樣本 → 欄位/型別/一般時段過濾/週契約排除正確；欄位破壞 → fail-fast；
FinMind 樣本解析 + token 缺失 → MissingTokenError。

## V3 — 交叉驗證（SC-004，離線）

```bash
.venv/bin/python -m pytest tests/test_verify_futures.py -q
```

一致樣本零告警；注入超差 → 告警列含兩源數值；token 缺失 → skipped 且記錄、退出碼 0。

## V4 — 消費端零改動（SC-006/007，離線）

```bash
.venv/bin/python -m pytest tests/test_real_data_integration.py -q
```

連續序列（含負價樣本）過 `validate_data_contract` 期貨門檻；taifex 源 instrument 之
監控訊息**無** MOCK 前綴；回測引擎直接消費連續序列跑通。

## V5 — 零回歸（SC-005）

```bash
.venv/bin/python -m pytest -q      # 全套（mock 路徑不變）
```

## V6 — 真實回填端到端（SC-001/008，**需網路**、預設跳過）

```bash
.venv/bin/python -m pytest -m network -q                       # network 標記測試
.venv/bin/python run_ingestion.py                              # TXF 真源回填（首次 ~12 分鐘）
.venv/bin/python run_ingestion.py --verify                     # 含 FinMind 交叉驗證（需 FINMIND_TOKEN）
.venv/bin/python run_backtest.py                               # TXF 以真資料回測
```

驗收：raw 表與連續表非零且區間相符；重跑冪等；TXF 回測產生真實績效
（**注意**：mock 基準數字自此不適用 TXF——屬預期的資料切換，非回歸）。

## 完成定義

V1–V5 全綠（CI 範圍）；V6 於本機網路環境驗收一次並將結果記入 tasks 完成註記；
`pytest -q`（不含 network）全綠為合併關卡。
