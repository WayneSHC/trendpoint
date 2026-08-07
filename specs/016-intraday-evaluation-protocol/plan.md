# Implementation Plan: 盤中時框評估協定

**Branch**: `claude/tradingview-mcp-analysis-1hnopa`（spec 目錄 `016-intraday-evaluation-protocol`）
| **Date**: 2026-08-07 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/016-intraday-evaluation-protocol/spec.md`

## Summary

把 2026-08-06 的一次性盤中探查升級為可重複的評估協定。技術路徑：
**三個純函式模組 + 一個 CLI 入口 + 一條週排程累積鏈**。

- 產出的權威格式是 **JSON 報告**，文字報表由它渲染——確定性（FR-003/SC-001）
  才有可逐欄比對的對象。
- 累積歷史以 **canonical CSV** 落地、經 Actions artifact 滾動傳遞（D1），
  價格於入庫時正規化至固定小數位，使指紋穩定、且資料源微幅飄動不會被
  誤判為衝突。
- 樣本外切分**自行實作**（gap-aware，無尋優）——`walk_forward.py` 的
  `WalkForwardAnalyzer` 內建網格尋優，而調參明列於本規格範圍外。
- 標的納入準則由**評估窗之前**的一段 lookback 計算，與評估期間不重疊，
  使 FR-010「不得引用回測產出」在時序上也成立。
- 對生產路徑零改動：新模組不被 `monitor_signals.py` / `backtester.py` 匯入，
  由靜態零引用測試焊死（沿用 spec 015 `tests/test_alert_outcomes.py` 的手法）。

## Technical Context

**Language/Version**: Python 3.10+（CI 矩陣 3.10 / 3.12）

**Primary Dependencies**: pandas、numpy、yfinance、pydantic v2、pytest
（**不新增任何相依**——CSV 落地即為避免引入 pyarrow，見 research.md R1）

**Storage**: canonical CSV（累積歷史，經 Actions artifact 滾動傳遞，不進版本庫）
＋ JSON 報告（artifact）。**不新增 SQLite 表**，不寫入 `trendpoint.db`。

**Testing**: pytest（新增 4 個測試檔；含 FR-019 的靜態零引用隔離測試）

**Target Platform**: 本機 CLI ＋ GitHub Actions runner（開發容器的 agent proxy
對 yfinance 回 403，取數一律在 runner 上進行——沿用 `research_b_segment.yml`
既有的理由）

**Project Type**: 研究用 CLI（單一 repo、扁平模組，與既有 `run_*.py` 同構）

**Performance Goals**: 單標的單次評估 < 60 秒；尺度掃描 ≤ 5 個尺度 × ≤ 12 檔
於 30 分鐘 job timeout 內完成。非熱路徑，無 Numba 需求。

**Constraints**:
- yfinance 5m 回溯上限 **60 天**（Yahoo 硬限制），故單次取數不可能覆蓋樣本外
  所需長度——累積是唯一路徑。
- Actions artifact 保留期上限 **90 天**（D1 已接受之代價）。
- 資料源對相同請求回傳浮動數值（實測：0050 相隔 56 分鐘得 7 → 5 個來回），
  故「可重現」只能定義為**對固定快照**可重現，不是對資料源可重現。

**Scale/Scope**: 每標的 ~3,130 根 / 59 交易日起算，週增 ~5 交易日；
候選標的 10–15 檔；累積歷史單檔 < 5 MB。

## Constitution Check

*GATE: 已於 Phase 0 前評估，並於 Phase 1 設計後複查。*

| 原則 | 判定 | 依據 |
|---|---|---|
| **I. 防禦看前偏誤**（NON-NEGOTIABLE） | ✅ 通過（有設計責任） | 本案不新增訊號計算，訊號一律經既有 `build_indicator_frame`，`.shift(1)` 語意不變。**本案自身的看前偏誤面在別處**：(a) 納入準則若用評估期間的資料判定納入，即為選擇偏誤的時序版本 → 準則改由**評估窗之前**的 lookback 計算（research.md R5）；(b) 合併衝突若採「後到者覆寫」，等於把日後修正的價格塞回早先的評估窗 → 採**先到者為準**（R3）。兩者皆納入 `tests/test_intraday_snapshot.py`。 |
| **II. 真實摩擦成本**（NON-NEGOTIABLE） | ✅ 通過 | 回測一律經 `BacktestEngine(config=cfg)`，費率單一來源仍為 `config.yaml` 的 `trading_cost`。本案不產生零成本數字。 |
| **III. 規格驗收對應測試** | ✅ 通過 | SC-001~SC-013 逐條對應測試，見 quickstart.md 的對照表；無法自動化者標 `[MANUAL]`。 |
| **IV. 效能紀律** | ✅ 通過（不適用熱路徑） | 合併/切分皆為向量化 pandas；尺度掃描為 O(尺度數) 次回測，屬研究批次而非百萬級熱路徑。 |
| **V. 組態集中化** | ✅ 通過 | 新增 `IntradayEvaluationConfig` Pydantic 模型 ＋ `config.yaml` 的 `intraday_evaluation` 區塊；門檻值一律進組態。既有 `structure_period=10` 硬編碼**不修**（FR-021 範圍外），但報告須顯式標示其為硬編碼值。 |
| **VI. 可重現性與資料衛生** | ⚠️ **有張力，已記錄於 Complexity Tracking** | 累積歷史是**不可再生成**的原始輸入（60 天窗一過即永久取不回），依原則 VI 的判準應**進版本庫**（spec 015 的 `alert_log/` 正是此判準的先例）。D1 裁決改置於 artifact，理由是 Yahoo 資料再散布疑慮。此為經裁決接受的偏離，代價是 90 天斷鏈風險。 |

**Gate 結論**：通過。唯一偏離（原則 VI）已於下方 Complexity Tracking 證成，
且該偏離源自使用者裁決而非實作便利。

**Phase 1 設計後複查**：無新增違反。設計過程新發現並已處理的兩點——

1. 原則 I 的適用面比初評更廣：納入準則的 lookback 位置（research.md R5）與
   合併衝突的取捨方向（R3）都是看前偏誤的變形。兩者都在設計階段就選了
   保守側，並各自有測試（SC-005、SC-007）。
2. 原則 V 有一個易被忽略的邊：效力標籤的門檻若可由呼叫端指定，FR-005 形同虛設。
   設計改為「門檻進 config，標籤本身是累積狀態的純函式、不可指定」（R6），
   使組態集中化與標籤不可竄改兩者兼得。

`.specify/scripts/bash/` 無 `update-agent-context.sh`，故 Phase 1 的
agent context 更新步驟無對象可執行。CLAUDE.md 的專案地圖待實作完成後
依 `.claude/docs/maintenance-protocol.md` 更新——現在寫入狀態描述會是假的。

## Project Structure

### Documentation (this feature)

```text
specs/016-intraday-evaluation-protocol/
├── spec.md              # 已完成
├── checklists/
│   └── requirements.md  # 已完成
├── plan.md              # 本檔
├── research.md          # Phase 0 產出
├── data-model.md        # Phase 1 產出
├── quickstart.md        # Phase 1 產出
├── contracts/
│   ├── accumulated-history.md   # 累積歷史 CSV 綱要
│   ├── evaluation-report.md     # JSON 報告綱要
│   └── cli.md                   # CLI 命令契約
└── tasks.md             # Phase 2（/speckit-tasks 產出，本命令不建立）
```

### Source Code (repository root)

```text
intraday_snapshot.py      # 新增：快照正規化/指紋、累積合併、衝突計數、
                          #       斷裂偵測、gap-aware 窗口切分（純函式）
intraday_universe.py      # 新增：標的納入準則（具版本、純函式）
intraday_report.py        # 新增：逐標的結果組裝、零交易成因分解、
                          #       離散度、效力標籤、JSON 產出與文字渲染
run_intraday_eval.py      # 新增：CLI 入口（取數/累積/評估/掃描）

config/
├── config.py             # 修改：新增 IntradayEvaluationConfig
└── config.yaml           # 修改：新增 intraday_evaluation 區塊

run_5m_evaluation.py      # 修改：verdict() 的既定處方改為量測驅動（FR-018）

.github/workflows/
├── intraday_accumulate.yml   # 新增：週排程，artifact 滾動累積 + 評估
└── probe_yfinance_5m.yml     # 修改：併入累積鏈，不再另存一套快照（FR-024）

tests/
├── test_intraday_snapshot.py   # 合併/衝突/斷裂/切分/確定性
├── test_intraday_universe.py   # 準則客觀性、擾動不敏感、排除理由可追溯
├── test_intraday_report.py     # 標籤、離散度、零交易分解、無有效性宣稱
└── test_intraday_isolation.py  # FR-019：生產路徑零引用 + 逐欄基準對照
```

**Structure Decision**: 沿用 repo 既有的**扁平模組**慣例（`ladder_system.py`、
`backtester.py`、`risk_gates.py` 皆位於根目錄），不新增套件目錄。
三個模組的切分依 FR 的自然分群——快照/累積（FR-004、013–016、022–024）、
納入準則（FR-009–012）、報告與標籤（FR-001–008、017–018、021）——
每群可獨立測試，且沒有一群小到該與另一群合併。CLI 只做編排，不含邏輯。

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| **偏離原則 VI**：不可再生成的原始輸入（累積歷史）不進版本庫 | D1 使用者裁決。Yahoo Finance 資料再散布的授權疑慮，與 `.gitignore` 排除 `data/` 同一理由 | 「進版本庫」（spec 015 `alert_log/` 的先例）最符合原則 VI 且無 90 天斷鏈風險，但它把授權疑慮直接落到版本庫裡。裁決選擇承擔斷鏈風險而非授權風險；FR-022/023 為此偏離的補償控制 |
| 新增 3 個模組而非 1 個 | FR 自然分為三群，且三群的測試對象不同（合併正確性 / 準則客觀性 / 報告不說謊） | 單一模組會讓「零引用隔離測試」失去粒度——無法區分「生產路徑誤引用了報告層」與「誤引用了快照層」，而前者無害、後者是未來函數入口 |
