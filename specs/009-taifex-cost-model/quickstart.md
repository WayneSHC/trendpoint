# Quickstart: 台指期成本/口數模型（008b）驗證指南

**Phase 1 產出** | 端到端驗證場景 V1–V6，對映 SC-001~007。
契約細節見 [contracts/cost-model-contracts.md](contracts/cost-model-contracts.md)、
欄位見 [data-model.md](data-model.md)。

## 前置

```bash
# repo 根目錄；venv 為 Python 3.10+（本機 .venv = 3.13）
.venv/bin/python -m pytest -q          # 既有基線應全綠再開工
```

## V1 — 現貨 parity（SC-001，硬關卡）

```bash
.venv/bin/python -m pytest -q                    # 全套綠（含既有 114+）
.venv/bin/python run_backtest.py                 # 數字對照下表，逐位元相同
```

| 標的 | 交易數 | 總報酬 | 勝率 | MDD |
|---|---|---|---|---|
| 2330.TW | 11 | 15.83% | 36.36% | −13.57% |
| 0050.TW | 13 | 5.43% | 61.54% | −8.23% |
| 00878.TW | 9 | 1.94% | 77.78% | −7.74% |
| 00919.TW | 5 | 10.00% | 60.00% | −5.36% |

（基準源：spec 007 `baseline-pre-mss.md` 校正後欄，008a 合併時已再驗一次。）

## V2 — 期貨成本數學（SC-002）

```bash
.venv/bin/python -m pytest tests/test_trading_costs.py -q
```

錨定數值例：TX 1 口 @20,000 點，單邊成本 = 定額 20 + 期交稅 20,000×200×0.00002 = 80
→ **100 NT$**；滑價 1 tick 反映於成交價 ±1 點（= 200 NT$/口 之不利偏移，不重複計費）。
MTX（12.5 + 50×稅基）、TMF（8.0 + 10×稅基）依乘數縮放。兩邊皆收（來回 ×2）。

## V3 — 保證金/口數（SC-003）

同上測試檔。錨定例：權益 1,000,000、收盤 20,000 點、TX、margin_rate 0.055、utilization 0.5
→ 每口保證金 220,000 → 口數 **2**；權益 200,000 → **0 口不進場**；
`partial_units(1, 0.5)` → 0（跳過平倉、保本照移）、`partial_units(3, 0.5)` → 1。

## V4 — 期貨端到端（SC-004 + SC-006）

```bash
.venv/bin/python -m pytest tests/test_futures_backtest_e2e.py tests/test_futures_backtest_guard.py -q
.venv/bin/python run_backtest.py                 # config 含 mock TXF/MTX 時：期貨照跑、不拋錯
```

斷言：mock TXF/MTX 回測跑通、`FuturesBacktestNotSupportedError` 不再拋出（護欄退役）、
總摩擦成本 > 0、全程口數為非負整數、權益曲線無 NaN、交易紀錄無空單（long-only）、
爆倉情境（極端 mock）正確終止並標記。

## V5 — 看前偏誤防線（SC-005）

```bash
.venv/bin/python -m pytest tests/test_lookahead_bias.py -q
```

新增斷言：截斷第 N 根之後的資料不改變第 N 根的 sizing 決策（口數）與成交價；
期貨成交發生於訊號次根（N+1）開盤 ± 滑價 tick。

## V6 — 費率 SoT 稽核（SC-007）

```bash
# 引擎/元件原始碼無硬編碼期貨費率常數（權威值只允許出現在 config.yaml 與測試錨定值）
grep -rnE '(0\.00002|exchange_fee|margin_rate)' --include='*.py' . | grep -v -E 'tests/|config/config\.py|\.venv'
# 期望：僅 schema 欄位定義／註解，無數值常數；config 載入後 Pydantic 驗證通過
.venv/bin/python -c "from config.config import load_config; c=load_config(); print(c.trading_cost.futures)"
```

## 完成定義

V1–V6 全數通過 + `pytest -q` 全綠（合併關卡）＋ 影響訊號邏輯之變更為零
（本 spec 不動訊號；若實作中發現訊號面被觸碰，附前後回測對照）。
