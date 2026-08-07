# Quickstart: 盤中時框評估協定

**Feature**: `016-intraday-evaluation-protocol` | **Date**: 2026-08-07

驗證本協定「確實做到它宣稱的事」的可執行步驟。實作細節見 tasks.md，
資料結構見 [data-model.md](./data-model.md)，介面見 [contracts/](./contracts/)。

## 前置

```bash
pip install -r requirements.txt   # 不新增相依（見 research.md R1）
pytest -q                          # 基線須全綠
```

**取數限制**：本開發容器的 agent proxy 對 yfinance 回 403。
需要真實取數的步驟一律在 GitHub Actions runner 上執行
（沿用 `research_b_segment.yml` 的既有理由）。本機驗證走 `--offline-csv-dir`。

**先產生示範累積歷史**（下列場景共用；合成資料，隨時可重新產生，
故刻意不提交進版本庫——憲章原則 VI）：

```bash
python tests/fixtures_016_intraday.py /tmp/demo_state
```

---

## 場景 1：確定性（US1 / SC-001）

```bash
python run_intraday_eval.py evaluate --state-dir /tmp/demo_state \
    --out-json /tmp/a.json
python run_intraday_eval.py evaluate --state-dir /tmp/demo_state \
    --out-json /tmp/b.json
python - <<'PY'
import json
a, b = (json.load(open(p)) for p in ("/tmp/a.json", "/tmp/b.json"))
for section in ("inputs", "results"):
    assert a[section] == b[section], f"{section} 不確定"
print("確定性 OK")
PY
```

**預期**：`inputs` 與 `results` 逐欄相同；`provenance` 允許不同。

---

## 場景 2：效力標籤與離散度（US1 / SC-002、SC-003）

```bash
python run_intraday_eval.py evaluate --state-dir /tmp/demo_state \
    --out-json /tmp/r.json
python - <<'PY'
import json
r = json.load(open("/tmp/r.json"))
for t in r["results"]["per_ticker"]:
    for k, v in t["performance"].items():
        assert isinstance(v, dict) and "validity_label" in v, f"{t['ticker']}.{k} 缺標籤"
for p in r["results"]["pooled"]:
    assert {"min", "max", "ratio"} <= p.keys(), f"{p['metric']} 缺離散度"
print("標籤與離散度 OK")
PY
```

**預期**：無裸數值績效欄；每筆 pooled 皆帶三欄離散度。
累積長度不足時所有標籤為 `in_sample_descriptive`。

---

## 場景 3：零交易可解釋（US1 / SC-004）

```bash
pytest -q tests/test_intraday_report.py -k zero_trade
```

**預期**：四種成因各自被正確分類，且完整管線（含納入準則）走到底時
零交易仍有成因。

⚠ **一個實作時才浮現的陷阱**：完全無波動的序列（`flat_frame`）不能拿來測
這條路徑——它的唯一價差為 0，`tick_ratio` 回傳 1.0，會在**納入準則**階段
就被排除，根本走不到評估層。用它測會拿到綠燈卻沒覆蓋到報告層。
完整管線的零交易須用 `quiet_frame`（保留一個微小但真實的檔位）。

---

## 場景 4：納入準則的擾動不敏感（US2 / SC-005、SC-006）

```bash
python run_intraday_eval.py universe --state-dir /tmp/demo_state
pytest -q tests/test_intraday_universe.py
```

**預期**：人為改變任一標的的回測輸出，`included` 清單不變；
每個被排除標的皆列出 `failed_criteria` 與 `measured` 實測值。

---

## 場景 5：合併與斷裂（US3 / SC-007、SC-009）

```bash
pytest -q tests/test_intraday_snapshot.py
```

**預期**：

- 兩份重疊快照合併後重複列 0、時序倒錯 0；重疊處保留先到值，
  衝突計數正確（research.md R3）。
- 前次累積取不回時 `chain_broken=true`、插入 `kind="chain_restart"` 的 Gap，
  且該事實出現在報告 `inputs`——不得靜默從零開始。

---

## 場景 6：窗口切分（US3 / SC-008）

```bash
pytest -q tests/test_intraday_snapshot.py -k window
```

**預期**：長度不足時 `splits == []`、`sufficient == false`、
`shortfall_trading_days > 0`；足夠時測試窗兩兩不重疊且不跨 Gap。

---

## 場景 7：尺度掃描（US4 / SC-010）

```bash
python run_intraday_eval.py evaluate --state-dir /tmp/demo_state \
    --scale-sweep --out-json /tmp/s.json
python -c "
import json; s=json.load(open('/tmp/s.json'))['results']['scale_sweep']
print('factors:', [x['factor'] for x in s])
print('conjunction:', [x['conjunction_passed'] for x in s])"
```

**預期**：輸出完整反應曲線。**曲線平坦即為「尺度不是瓶頸」的證據**，
此時正確產出是刪除該假設，不是實作參數時框化（FR-018）。

---

## 場景 8：生產路徑零改動（SC-011）

```bash
pytest -q tests/test_intraday_isolation.py
python run_backtest.py   # 與實作前逐筆逐欄相同
```

**預期**：靜態零引用檢查通過（`monitor_signals.py` / `backtester.py` /
`ladder_system.py` 皆未 import 本案任一模組）；日線回測輸出與基準 fixture 逐欄相同。

---

## 場景 9：無有效性宣稱（SC-012）

```bash
pytest -q tests/test_intraday_report.py -k efficacy
```

**預期**：報告全文的措辭清單命中數為 0
（清單見 `contracts/evaluation-report.md`）。

---

## 場景 10：Actions 累積鏈 `[MANUAL]`

無法本機自動化（需真實 runner 與跨 run 的 artifact 傳遞）。人工步驟：

1. 手動觸發 `intraday_accumulate.yml`，確認產出 artifact 且
   `chain_state.json` 的 `chain_broken` 為 `true`（首次執行）。
2. 再次觸發，確認 `chain_broken` 轉為 `false`、`bars_added > 0`、
   `chain_origin` 沿用首次值。
3. 檢視報告 `inputs.actual_span`——須為**實得**期間而非請求期間。
4. 確認 artifact `retention-days` 為 90，且排程 cron 為每週一次（FR-022）。

---

## 驗收標準 ↔ 驗證方式對照

| SC | 驗證 |
|---|---|
| SC-001 | 場景 1；`tests/test_intraday_report.py::test_determinism` |
| SC-002 | 場景 2；`test_intraday_report.py::test_every_perf_has_label` |
| SC-003 | 場景 2；`test_intraday_report.py::test_pooled_has_dispersion` |
| SC-004 | 場景 3；`test_zero_trade_cause_exhaustive` + `test_zero_trade_survives_full_pipeline` |
| SC-005 | 場景 4；`test_intraday_universe.py::test_perturbation_insensitive` |
| SC-006 | 場景 4；`test_intraday_universe.py::test_exclusion_traceable` |
| SC-007 | 場景 5；`test_intraday_snapshot.py::test_merge_no_dup_no_disorder` |
| SC-008 | 場景 6；`test_window_insufficient_reports_shortfall` / `test_window_splits_disjoint_when_sufficient` / `test_window_does_not_cross_gap` |
| SC-009 | 場景 5；`test_intraday_snapshot.py::test_accumulate_offline_builds_chain` + `test_intraday_report.py::test_chain_break_surfaces_in_report` |
| SC-010 | 場景 7；`test_scale_sweep_curve` + `test_verdict_requires_measurement` |
| SC-011 | 場景 8；`test_production_path_does_not_import_feature_modules` + `test_daily_production_path_unchanged` |
| SC-012 | 場景 9；`test_no_efficacy_claims_in_json` / `_in_text` / `_under_every_label` |
| SC-013 | `pytest -q` 全綠；本表即「每條 SC 對應測試」的證據 |
| 場景 10 | `[MANUAL]` —— 跨 run 的 artifact 傳遞無法在單機測試中重現 |
