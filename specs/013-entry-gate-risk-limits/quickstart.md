# Quickstart / 驗收指引: 進場閘門（回撤上限 + 結算日封鎖）

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Date**: 2026-07-30

驗收切為兩段：**A 段可在無市場資料的環境（含 CI）完整完成**，
**B 段必須在有 `trendpoint.db` 的本機執行**。

---

## A 段：離線驗收（無需真實資料）

### 執行

```bash
pytest -q                                          # 全綠為硬性關卡
pytest -q tests/test_risk_gates.py -v              # 純元件（狀態機 + 結算日）
pytest -q tests/test_entry_gate_integration.py -v  # 引擎接線
pytest -q tests/test_lookahead_bias.py -v          # 看前偏誤防禦
pytest -rs                                         # 逐條檢查 skip 理由
```

**注意 `pytest -rs`**：本 repo 的踩坑教訓是「該跑卻跳過等於沒驗到」。
本案所有 A 段測試一律以合成資料執行，**不得**因缺 `trendpoint.db` 而 skip；
若出現 skip，即代表 A 段設計失敗。

### 合成資料在本案比真實資料更好

A 段的核心測試都需要**精確控制觸發條件**，合成資料在此優於真實資料：

| 測試 | 需要的序列 | 合成方式 |
|---|---|---|
| SC-002 回撤封鎖與恢復 | 連續虧損跨過門檻，之後回升 | 構造單調下跌段 + 回升段 |
| SC-003 封鎖中出場仍執行 | 封鎖中持倉且觸發停損 | 先建倉 → 令權益跌破門檻 → 令價格觸及吊燈停損 |
| SC-005 schema 拒絕 | 不需序列 | 直接建構 config 物件 |
| SC-007 假日後推 | 第三個週三缺席的索引 | 構造索引時刻意移除該日 |

真實資料反而無法保證這些邊界情境一定出現。

### SC-001 的離線驗收法（位元不變性，比 spec 012 更嚴）

本案改變**權益路徑**，故比對範圍須含權益曲線逐根值：

```bash
# 1. 改碼前，以固定合成序列跑回測，存下逐筆 trades 與 equity_curve 全欄
git stash                       # 或 checkout base commit
# → 存為入版控的期望檔（tests/fixtures/013_baseline_*.csv）

# 2. 改碼後（兩道閘門皆關閉、預設）以同序列同參數重跑
git stash pop

# 3. 比對三層，差異數皆須為 0：
#    (a) 逐筆 trades（時點/方向/口數/損益）
#    (b) equity_curve 逐根 equity 值
#    (c) equity_curve 欄位集（證明 block_reason 未在關閉時輸出）
```

第 (c) 層是本案特有的——條件輸出欄若被誤實作成恆定輸出，這一層會抓到。

---

## B 段：需真實資料（本機執行，`[MANUAL]`）

### 前置

```bash
python run_ingestion.py            # 建立/更新 trendpoint.db
```

### SC-015：先校準門檻，再跑對照（順序不可顛倒）

```bash
python run_backtest.py             # 輸出含 monte_carlo 重抽分布
```

從輸出的 `最大回撤分布` 取**最深一側**作為封鎖門檻的參考起點，
並把選擇依據寫進 spec 的 SC-015 條目。

> **取哪一端（已踩過的坑）**：本規格全篇所稱「p95 回撤」指的是**回撤幅度**的
> 第 95 百分位，即「二十次裡最壞的那一次」。但 `monte_carlo` 的 `max_drawdown`
> 是**帶號負值**，所以幅度的 p95 對應到帶號分布的**第 5 百分位**——
> `np.percentile(mdds, 95)` 取到的反而是最淺的那一側。
>
> `run_b_segment.py` 初版就取錯了端，導致每檔標的都校準出 `0.00%` 的門檻
> （深尾稀有虧損的分布下，上緣多半是「完全沒有回撤」的幸運路徑），
> 而報表看起來一切正常。`monte_carlo.format_monte_carlo_report` 的輸出末行
> 早已寫明正確慣例：「風險預算應以回撤分布的 5 百分位（最深一側）為準」。

**為什麼順序不可顛倒**：若先跑對照再挑門檻，你會挑到「在這份歷史上剛好
避開最大那次回撤」的值——那是後見之明，不是風險預算。`monte_carlo` 的
深尾是對**未曾發生但可能發生**的回撤的估計，這才是門檻該對齊的東西。

### SC-014：前後回測對照

```bash
# 1. 基準（兩道閘門關閉，config 預設）
python run_backtest.py
python run_ablation.py 0050.TW

# 2. 於 config/config.yaml 啟用（門檻用 SC-015 校準值）後重跑
python run_backtest.py
python run_ablation.py 0050.TW

# 3. 期貨路徑（含空方與結算日閘門；需 TXF 資料）
#    對 TXF 於 ticker_overrides 設 enable_short: true 與兩道閘門
python run_backtest.py
```

**記錄項**（各組，扣成本後）：**MDD、Calmar、期望值、Profit Factor、
交易筆數、總報酬**。

### 判讀原則（本案與 spec 012 不同，務必先讀）

| 觀察 | 結論 |
|---|---|
| MDD 改善、Calmar 改善，總報酬下降 | **這是預期且良好的結果** → 閘門有效 |
| MDD 未改善 | 閘門無效（門檻太寬或觸發太晚）→ 維持關閉，記錄實測 |
| 交易筆數大幅下降而 MDD 僅微幅改善 | 閘門在殺樣本 → 門檻太緊，或本策略的回撤不是連續型 |
| 總報酬上升 | 需警惕：可能是門檻剛好避開特定歷史事件（過度配適徵兆），須以 walk-forward 確認 |

**FR-014 的重點在此**：本案預期降低總報酬。若以總報酬裁決，
會把一個有效的風控功能誤判為有害而砍掉——這個判讀基準必須在跑之前就講定。

### 採用決策的額外門檻

```bash
python run_walk_forward.py         # out-of-sample 確認
```

單次回測對照不足以支撐預設啟用，門檻值尤其容易被後見之明挑選。

---

## SC ↔ 驗收方式對照表（憲章原則 III）

| SC | 內容 | 驗收方式 | 段 |
|---|---|---|---|
| SC-001 | 關閉時逐筆＋逐根＋欄位集一致 | pytest（合成資料 + 入版控期望檔） | A |
| SC-002 | 回撤封鎖與恢復（可指出確切根數） | pytest（構造下跌+回升序列） | A |
| SC-003 | **封鎖中出場仍執行** | pytest（封鎖中觸發停損） | A |
| SC-004 | 篡改判定根之後不改判定 | pytest（`test_lookahead_bias.py`） | A |
| SC-005 | 恢復門檻 ≥ 封鎖門檻被拒 | pytest（schema 驗證） | A |
| SC-006 | 結算日不進場、非結算日不受影響 | pytest（純函式 + 整合） | A |
| SC-007 | 第三個週三為假日則後推 | pytest（構造缺席索引） | A |
| SC-008 | 現貨啟用結算日閘門無效果且不報錯 | pytest | A |
| SC-009 | 兩道閘門獨立性 | pytest（各單開一次） | A |
| SC-010 | 空方鏡像對稱 | pytest（價格鏡像資料） | A |
| SC-011 | 消融兩新列可比較（含 Calmar/MDD） | pytest ＋ 實跑（合成資料） | A |
| SC-012 | 封鎖原因可辨識 | pytest（`block_reason` 欄值） | A |
| SC-013 | `pytest -q` 全綠 | 實跑（含 `-rs` 檢查 skip） | A |
| SC-014 | 前後回測對照 | **`[MANUAL]`** 本機實跑，數字回填 spec | **B** |
| SC-015 | 門檻以 p95 回撤校準 | **`[MANUAL]`** 本機實跑，依據回填 spec | **B** |

## 完成定義

- **A 段完成**即可合併（兩道閘門預設關閉，對既有行為零影響）。
- **B 段未完成前**：`use_dd_gate` / `use_settlement_gate` 維持 `false`、
  spec Status 不得標為 Implemented、**不得在任何文件宣稱「改善了風險」或
  「降低了回撤」**。
