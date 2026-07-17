# Quickstart: 台指期做空（003）驗證指南

**Phase 1 產出** | V1–V6 對映 SC-001~008。契約見
[contracts/short-side-contracts.md](contracts/short-side-contracts.md)。

## 前置

```bash
.venv/bin/python -m pytest -q          # 基線 133 綠（008b 完成時）再開工
```

## V1 — 零回歸雙保證（SC-003，硬關卡）

```bash
.venv/bin/python -m pytest -q          # 全套綠
.venv/bin/python run_backtest.py       # 預設 config（enable_short=false）
```

| 標的 | 基準（008b 完成時） |
|---|---|
| 2330.TW | 15.83% / 11 筆 / 36.36% / −13.57% |
| 0050.TW | 5.43% / 13 筆 / 61.54% / −8.23% |
| 00878.TW | 1.94% / 9 筆 / 77.78% / −7.74% |
| 00919.TW | 10.00% / 5 筆 / 60.00% / −5.36% |
| 00631L.TW | 36.44% / 7 筆 / 71.43% / −6.71% |
| **TXF（mock，long-only）** | **46.74% / 4 筆**（進場 4、max 3 口、保證金 max 592,138） |
| **MTX（mock，long-only）** | **9.16% / 1 筆**（10 口、469,839） |

現貨 + 期貨 long-only 數字**逐位元不變**。

## V2 — 鏡像對稱（SC-002）

```bash
.venv/bin/python -m pytest tests/test_short_side.py -q
```

(a) 數值鏡像變換：`make_klines` 翻轉（p'=2C−p、high↔low、量能不變）→ 原序列多方
交易序列與翻轉序列空方交易一一對應（根位、口數規則、事件鏡像）。
(b) 手工情境對：1 口 floor=0（跳過回補、保本照移）、爆倉方向、吊燈只降不升、止損上穿。

## V3 — 空方端到端（SC-001）

```bash
.venv/bin/python -m pytest tests/test_short_futures_e2e.py -q
```

含下跌段的期貨序列 + `enable_short=true` → ≥1 筆空方交易（SELL_SHORT→COVER_ALL）、
成本非零（兩邊定額+稅）、口數全程非負整數、**無借券費欄位**、確定性。

## V4 — 空方爆倉 + 看前偏誤（SC-005/006）

```bash
.venv/bin/python -m pytest tests/test_short_futures_e2e.py tests/test_lookahead_bias.py -q
```

空頭持倉遇急漲（進場後嫁接 +10%/根）→ 權益 ≤ 0 當根強制回補、曲線截止、標記爆倉。
Lookahead：空方進場之截斷不變性、SELL_SHORT 成交 = N+1 開盤 − 滑價 tick（不利向下）、
sizing 用訊號根收盤權益。

## V5 — 硬邊界與裁決（SC-004）

```bash
.venv/bin/python -m pytest tests/test_short_side.py -q
```

現貨 ticker override 明設 enable_short=true → config 載入 ValueError；
equity 回測任何旗標組合零空單；同根多空訊號 → 三關價唯一裁決。

## V6 — 推播 dry-run（SC-008）

```bash
.venv/bin/python -m pytest tests/test_monitor_short.py -q
.venv/bin/python monitor_signals.py --once     # 人工目檢：期貨列入迭代、mock 標示
```

mock 期貨空方訊號：檢測 → 格式化（含方向 + 【MOCK 資料—dry-run】前綴）→ Mock
通知端送達；去重行為不變。

## 完成定義

V1–V6 全過 + `pytest -q` 全綠；007 spec 之 BLOCKED-003 註記移除（SC-007）；
影響訊號邏輯之變更為零（空方為新增路徑、多方逐字不動——V1 位元對照為證）。
