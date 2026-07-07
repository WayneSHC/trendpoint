# TrendPoint Constitution

## Core Principles

### I. 防禦看前偏誤（Look-Ahead Bias Defense）— NON-NEGOTIABLE
所有交易訊號的判斷，僅能使用「決策當下已收盤」的歷史 K 線數據：

- 任何滾動視窗（rolling window）結構點計算，必須加上 `.shift(1)` 後才可參與訊號判斷。
- 回測引擎中，訊號於第 N 根 K 線產生時，成交價一律使用第 N+1 根的開盤價（或明確標註的等效機制）。
- 每個新增的訊號或指標，必須在 `tests/test_lookahead_bias.py`（或對應測試檔）中新增偏誤防禦測試後方可合併。

### II. 真實摩擦成本（Realistic Friction Costs）— NON-NEGOTIABLE
所有回測與績效報告，必須計入手續費（commission）、證交稅（tax）與滑價（slippage），
費率以 `config/config.yaml` 之 `trading_cost` 區塊為唯一真實來源（single source of truth）。
禁止展示或提交「零成本」的績效數字，除非明確標註為消融測試（ablation）用途。

### III. 規格與驗收標準必須可測試（Spec Criteria Must Map to Tests）
每一條規格（`specs/**/spec.md`）中的驗收標準（Acceptance Criteria），必須對應至少一個
pytest 測試；無法自動化者，須在規格中明確標註 `[MANUAL]` 並說明人工驗證步驟。
規格與程式碼發生歧異時：先修規格或先修程式，二擇一，不允許沉默漂移（silent drift）。

### IV. 效能紀律（Performance Discipline）
核心演算法熱路徑（hot path）依序採用：Pandas 向量化 → NumPy 向量化 → Numba `@jit`。
禁止在百萬級 K 線回測路徑上使用純 Python 迴圈或 `DataFrame.apply()`。
Numba 屬於選配加速：所有 `@jit` 函式必須具備無 Numba 環境下的自動降級回退，
且降級後計算結果須與加速版完全一致。

### V. 組態集中化（Centralized Configuration）
所有策略參數（ATR 週期、階梯 k 值、吊燈乘數、時間止盈等）一律定義於
`config/config.yaml`，經由 Pydantic 模型驗證後載入。禁止在演算法、回測或 UI
程式碼中硬編碼（hardcode）可調參數；新參數必須同時更新 Pydantic schema 與預設值。

### VI. 可重現性與資料衛生（Reproducibility & Repo Hygiene）
- 版本庫只追蹤「原始輸入」與「程式碼／規格」；所有可再生成的產物
  （回測 equity/trades CSV、SQLite 資料庫、日誌檔）一律列入 `.gitignore`。
- 資料擷取須通過資料契約驗證（`validate_data_contract`）：欄位齊全、時序遞增、
  無負值價格；遇缺漏 K 線採向前填補並記錄警告，遇極端離群值須過濾並發出警告。

## Security & Operational Constraints

- 通知憑證（Telegram token 等）僅能經由環境變數或 GitHub Actions Secrets 注入，
  嚴禁寫入版本庫（含測試 fixture）。
- SQLite 存取一律使用參數化查詢（`db_security.py` / `security_utils.py` 之既有防護），
  禁止字串拼接 SQL。
- 排程監控（GitHub Actions）必須具備告警去重（deduplication）機制，
  且允許快取失效時的重複告警視為可接受的降級行為，但不得漏發。

## Development Workflow & Quality Gates

1. 新功能一律走 Spec Kit 流程：`/speckit-specify` → （必要時 `/speckit-clarify`）→
   `/speckit-plan` → `/speckit-tasks` → `/speckit-implement`，完成後以 `/speckit-analyze`
   檢查規格 ↔ 程式一致性。
2. 合併前 `pytest` 全綠為硬性關卡（CI 於 push / PR 自動執行）。
3. 任何影響訊號邏輯的變更，必須附上前後回測對照（同一資料、同一成本假設）。
4. UI（Streamlit）層不得內嵌演算法邏輯；演算法一律位於可獨立測試的模組
   （`ladder_system.py` 等），UI 僅負責呈現。

## Governance

本憲法優先於其他開發慣例。修訂需以 PR 形式提出，說明動機與影響範圍，
並同步更新受影響的規格與模板。所有 code review 必須檢核是否違反核心原則；
違反 NON-NEGOTIABLE 原則（I、II）的變更一律退回。
複雜度必須被證成：若新增抽象層或依賴，PR 說明中須回答「為何更簡單的做法不可行」。

**Version**: 1.0.0 | **Ratified**: 2026-07-07 | **Last Amended**: 2026-07-07
