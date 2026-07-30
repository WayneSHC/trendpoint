# Phase 1 Data Model: BOS 續勢進場的量能確認濾網

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Date**: 2026-07-30

本案不觸碰任何持久化 schema（無資料表變更、無入庫欄位）。以下為**記憶體內
衍生欄**與**組態參數**兩類實體的定義。

## 1. 衍生欄：`bos_volume_ok`

| 屬性 | 值 |
|---|---|
| 名稱 | `bos_volume_ok` |
| 型別 | `bool`（pandas `Series[bool]`，無 NaN） |
| 產生位置 | `ladder_system.build_indicator_frame()` |
| 存在條件 | 僅當 `use_bos_volume=True`；否則**不輸出**（見 research.md D2） |
| 消費者 | `backtester.py`（BOS 進場分支）、`monitor_signals.py`（BOS 告警） |
| 持久化 | 無。不入 `trendpoint.db`、不入回測產物的必要欄位 |

### 計算定義

```text
vol_ma(i) = mean(volume[i-N .. i-1])        # 僅判定根之前，N = bos_volume_period
bos_volume_ok(i) = notna(vol_ma(i))
                 AND vol_ma(i) > 0
                 AND volume(i) > vol_ma(i) × bos_volume_mult
```

實作對應 `volume.rolling(N).mean().shift(1)`。`.shift(1)` 是憲章原則 I 的
落點；判定根自身的 `volume(i)` 可用（該根已收盤，成交發生於第 i+1 根開盤），
與既有 MSS displacement 同一慣例（`ladder_system.py:246-247`）。

### 值域與邊界

| 情境 | `bos_volume_ok` | 依據 |
|---|---|---|
| 前 N 根（rolling 未滿，`vol_ma` 為 NaN） | `False` | FR-007、SC-005 |
| `vol_ma == 0`（連續零量） | `False` | FR-007（否則門檻退化為「一律通過」） |
| `volume` 為 0 | `False` | FR-007（`0 > 正數 × mult` 恆為 False） |
| `volume` 恰等於門檻 | `False` | 嚴格大於，與 MSS displacement 一致 |
| 其餘 | `volume > vol_ma × mult` | FR-001 |

### 時序契約（spec 004 不變式）

新欄必須滿足 spec 004 的**前綴一致性**
（`specs/004-acceptance-tests/contracts/indicator-frame.md`）：

```text
build_indicator_frame(df.iloc[:i], **p).iloc[-1]['bos_volume_ok']
  == build_indicator_frame(df, **p).iloc[i-1]['bos_volume_ok']
```

`rolling(N).mean().shift(1)` 天然滿足此性質（只依賴 `df.iloc[:i]`）。
須將該欄加入 `tests/test_acceptance_parity.py:30` 的 `PARITY_COLUMNS`
（僅在啟用參數的測試組），使此不變式被自動驗證。

### 方向性

**無方向**。同一欄同時服務多方與空方續勢進場——量能放大不分漲跌
（FR-006）。這與 `regime_ok` / `regime_ok_short` 需要兩欄的情形不同，
後者的長均線分量本身有方向。

## 2. 組態參數（新增三項）

全部置於 `config/config.yaml` 的 `strategy.default`，並可經
`strategy.ticker_overrides.<ticker>` 覆寫（FR-009）。Pydantic schema 位於
`config/config.py`（與既有 `mss_volume_mult` 同一模型）。

| 參數 | 型別 | 預設 | 值域 | 說明 |
|---|---|---|---|---|
| `use_bos_volume` | `bool` | `False` | — | 是否啟用 BOS 續勢進場的量能確認。**預設關閉**（FR-002） |
| `bos_volume_mult` | `float` | `1.5` | `> 0` | 門檻乘數：`volume > 均量 × 此值` |
| `bos_volume_period` | `int` | `20` | `>= 2` | 平均量回看根數（不含判定根） |

### 與既有參數的關係

- **獨立於 `mss_volume_mult`**（FR-003）。兩者分別服務反轉訊號的位移確認與
  續勢進場的量能確認，可分別調整、互不影響。
- **不綁定 `structure_period`**。後者硬編碼於三處呼叫端且值為 10，與函式
  宣告預設 20 不一致；綁定會使量能回看期無法單獨消融（見 research.md D4）。

### 值域驗證

`bos_volume_mult` 須 `> 0`（`gt=0`）：`<= 0` 會使條件對任何正量恆成立，
等於濾網形同不存在卻顯示為啟用——屬應被 schema 擋下的無意義設定。
`bos_volume_period` 須 `>= 2`（`ge=2`，比照既有 `adx_period` / `ma_period`）。

## 3. 進場判定維度（既有實體的擴充）

`check_entry_signal` 的確認維度由五道增為六道（**僅在續勢分支**）：

| 維度 | 既有/新增 | 消融鍵 |
|---|---|---|
| 結構（BOS/MSS 方向） | 既有 | `structure` |
| 動能（收紅/黑 K） | 既有 | `momentum` |
| 趨勢（當日開盤價／VWAP） | 既有 | `trend` |
| 波動（振幅 > 1.2×ATR） | 既有 | `volatility` |
| 全域（三關價 + 市況） | 既有 | `global` |
| **量能（續勢分支限定）** | **新增** | **`bos_volume`** |

判定式：`volume_conf_ok = volume_ok or ('bos_volume' in disabled_filters)`，
其中 `volume_ok: bool = True` 為新增關鍵字參數（預設值使 14 個既有呼叫點
零改動，且語意正確——它們都不套用此濾網）。

反轉（MSS）分支不傳 `volume_ok`，故恆為 `True`，FR-005 由呼叫點自然成立。

## 4. 消融清單項

`run_ablation.py` 的 `ABLATION_TARGETS` 新增一列：

```text
("停用 BOS 量能確認", "bos_volume")
```

**前提**：消融的意義是「相對基準關掉某道濾網」，故執行消融時
`use_bos_volume` 必須為 `True`（否則該列與基準列完全相同、無資訊）。
此前提須在 `run_ablation.py` 的輸出中明示（例如濾網未啟用時於該列標註
「未啟用，略過」），不得靜默產出一列與基準相同的數字誤導判讀。
