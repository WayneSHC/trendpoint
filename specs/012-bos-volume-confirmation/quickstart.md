# Quickstart / 驗收指引: BOS 續勢進場的量能確認濾網

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Date**: 2026-07-30

驗收切為兩段：**A 段可在無市場資料的環境（含 CI）完整完成**，
**B 段必須在有 `trendpoint.db` 的本機執行**。這是使用者明示的環境約束。

---

## A 段：離線驗收（無需真實資料）

### 前置

```bash
pip install -r requirements.txt     # 或既有 .venv
```

### 執行

```bash
pytest -q                                          # 全綠為硬性關卡
pytest -q tests/test_bos_volume_confirmation.py -v # 本案新測試
pytest -q tests/test_lookahead_bias.py -v          # 看前偏誤防禦
pytest -q tests/test_acceptance_parity.py -v       # 前綴一致性（含新欄）
pytest -rs                                         # 逐條檢查 skip 理由
```

**注意 `pytest -rs`**：本 repo 有一條踩坑教訓——「該跑卻跳過等於沒驗到」。
新測試若因缺 DB 而 skip，即代表 A 段設計失敗（A 段測試一律應以合成資料執行，
不得依賴 `trendpoint.db`）。

### 合成資料的產生方式

不得依賴市場資料。三種可用來源，優先序如下：

1. `data_sources/mock_source.py`（既有 mock adapter，可重現的 rng）
2. `tests/acceptance_fixtures.py`（既有測試 fixture）
3. 測試內就地構造的最小 DataFrame（用於真值表類測試）

### SC-001 的離線驗收法（位元不變性）

FR-002 的「與實作前逐筆一致」不需要真實資料——它驗證的是**位元不變性**：

```bash
# 1. 在實作前的 commit 上，以固定合成序列跑回測並存下逐筆交易
git stash                       # 或 git checkout <base-commit>
python - <<'PY'
# 以 mock 資料跑 BacktestEngine.run_backtest，將 trades DataFrame 存為 CSV
PY

# 2. 回到實作後（濾網預設關閉），以同一序列同一參數重跑
git stash pop

# 3. 逐筆比對：進出場時點、方向、股數/口數、損益、權益曲線終值
#    差異數必須為 0
```

此比對建議直接寫成 pytest（以 fixture 固定基準值），使其進入 CI 常態守門，
而非一次性手動步驟。

---

## B 段：需真實資料（本機執行，`[MANUAL]`）

### 前置

```bash
python run_ingestion.py            # 建立/更新 trendpoint.db
```

### SC-010：前後回測對照與採用決策

```bash
# 1. 基準（濾網關閉，config 預設）
python run_backtest.py
python run_ablation.py 0050.TW

# 2. 於 config/config.yaml 設 use_bos_volume: true 後重跑
python run_backtest.py
python run_ablation.py 0050.TW

# 3. 期貨路徑（含空方鏡像；需 TXF 資料）
#    對 TXF 於 ticker_overrides 設 enable_short: true 後執行
python run_backtest.py
```

**記錄項**（啟用/停用兩組，扣成本後）：交易筆數、期望值、Profit Factor、
最大回撤（MDD）、勝率（**僅輔助觀察**）。

**判讀原則**（沿用 `run_ablation.py` 開頭載明者）：

| 消融結果 | 結論 |
|---|---|
| 停用後績效明顯惡化 | 濾網貢獻期望值 → 可考慮預設啟用 |
| 停用後績效不變或更好，且交易筆數明顯增加 | 濾網只在扼殺樣本 → **維持關閉**並記錄「實測無益」 |
| 差異在雜訊範圍內 | 樣本不足以裁決 → 維持關閉，另跑 walk-forward |

**無論結果有利與否，都必須把實測數字回填至 spec 的 SC-010 條目**
（先例：spec 007 的 SC-003 未達成如實記錄）。

### SC-011：monitor 與回測判定一致

```bash
# 濾網啟用後執行一次訊號檢測（無憑證環境走 Mock 分支）
python monitor_signals.py --once
```

比對同一資料下 monitor 對「續勢進場候選」的判定與回測一致
（量能未達門檻的 BOS 不得推播為進場候選）。

### 採用決策的額外門檻

SC-010 的單次回測對照**不足以**支撐預設啟用。若初步結果有利，
需再跑 out-of-sample 確認：

```bash
python run_walk_forward.py
```

---

## SC ↔ 驗收方式對照表（憲章原則 III）

| SC | 內容 | 驗收方式 | 段 |
|---|---|---|---|
| SC-001 | 關閉時逐筆一致 | pytest（合成資料 + 固定基準 fixture） | A |
| SC-002 | 訊號序列不變 | pytest（同資料兩種設定比對 `bos_signal`/`mss_signal`） | A |
| SC-003 | 僅量能未達門檻時不進場 | pytest（構造 K 線，其餘四道確認皆通過） | A |
| SC-004 | 空方鏡像對稱 | pytest（`test_short_side.py` 真值表增維） | A |
| SC-005 | 暖機期不進場 | pytest（前 N 根無進場） | A |
| SC-006 | 篡改未來量不改判定 | pytest（`test_lookahead_bias.py` + parity 前綴一致性） | A |
| SC-007 | 消融清單新列可比較 | pytest（清單含該鍵）＋ 實跑（合成資料） | A |
| SC-008 | 反轉分支判定不變 | pytest（MSS 分支不受 `volume_ok` 影響） | A |
| SC-009 | `pytest -q` 全綠 | 實跑（含 `-rs` 檢查 skip） | A |
| SC-010 | 前後回測對照與採用決策 | **`[MANUAL]`** 本機實跑，數字回填 spec | **B** |
| SC-011 | monitor 判定一致 | **`[MANUAL]`** `monitor_signals.py --once` | **B** |

## 完成定義

- **A 段完成**即可合併（濾網預設關閉，對既有行為零影響）。
- **B 段未完成前**：`use_bos_volume` 維持 `false`、spec Status 不得標為
  Implemented、不得在任何文件宣稱「已提升勝率或期望值」。
