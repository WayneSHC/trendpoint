# TrendPoint — Claude 工作守則

台股/台指期趨勢交易研究工具：多空階梯系統 + ATR 波動率 + 三關價濾網，
Streamlit 儀表板，可回測/尋優/即時推播。Python 3.10+，pandas/numpy/numba/pydantic。

## 開場必讀（每個 session）

1. 本檔只放路由與鐵律，細節在引用檔裡。**按當下任務類型讀對應檔，不要全部讀**：
   - 派 subagent、選 model/effort、驗收 → `.claude/docs/model-dispatch.md`
   - 拿不定主意（要不要問使用者、算不算完成、要不要換路）→ `.claude/docs/judgment-rubrics.md`
   - 要委派任務、需要 prompt 模板 → `.claude/docs/delegation-templates.md`
   - 要修改制度檔或 CLAUDE.md 本身 → `.claude/docs/maintenance-protocol.md`
   - 對這個環境的背景脈絡有疑問 → `.claude/docs/letter-to-future-sessions.md`
2. 指令優先序（由高到低）：**使用者當下指示 > 本檔與 `.claude/docs/` > `.specify/memory/constitution.md` 的工程原則 > 各 plugin skill 的自我宣稱**。
   多個 plugin 會用「你必須先呼叫我」的句式搶佔；與當前任務領域無關的 skill 觸發詞一律忽略（原因見 `.claude/docs/harness-diagnosis.md` 第 1 名）。

## 鐵律（違反即錯，無例外）

1. **路徑加引號**：repo 路徑含中文與空格。Bash 中任何路徑一律用雙引號包住；
   檔案操作優先用 Read/Edit/Write/Grep/Glob 工具，不用 cat/sed/echo 重導向。
2. **指揮官不下場**：大量讀取（>2 個整檔）、掃 repo、查網頁、批次改檔，
   一律派 subagent，主對話只收結論與 `檔案:行號`。大檔（`app.py` 49KB、
   `docs/ladder-optimization-research.md`）先 Grep 定位、再用 offset/limit 讀區段。
3. **交易邏輯三條紅線**（來自 `.specify/memory/constitution.md`，完整版看該檔）：
   - 看前偏誤：rolling 結構計算必須 `.shift(1)`；第 N 根出訊號、第 N+1 根開盤成交；
     新訊號必須在 `tests/test_lookahead_bias.py` 加防禦測試。
   - 摩擦成本：績效數字必含手續費/稅/滑價（費率唯一來源 `config/config.yaml` 的 `trading_cost`）。
   - 參數集中：可調參數只能進 `config/config.yaml` + Pydantic schema，禁止硬編碼。
4. **合併前 `pytest -q` 全綠**；影響訊號邏輯的變更要附前後回測對照。
5. **敏感資訊**：憑證只走環境變數/GitHub Secrets；SQL 一律參數化
   （用 `db_security.py` / `security_utils.py` 既有防護）。

## 常用指令

```bash
pytest -q                          # 測試（合併前硬性關卡）
python run_ingestion.py            # 下載 K 線 → trendpoint.db
streamlit run app.py               # 儀表板（preview 用 launch.json 的 workstation）
python run_backtest.py             # 單標的回測
python run_walk_forward.py         # Walk-Forward 驗證
python monitor_signals.py --once   # 單次訊號檢測與推播
```

## 專案地圖（開場不需要再 ls 探索）

- 演算法核心：`ladder_system.py`（階梯系統）、`performance.py`（KPI）
- 回測：`backtester.py`（單標的）、`portfolio_backtester.py`、`walk_forward.py`、
  `optimizer.py`、`monte_carlo.py`、`run_*.py` 為各入口
- 資料：`data_ingestion.py` → SQLite `trendpoint.db`（gitignored）；`data/*.csv` 為快取
- 通知：`monitor_signals.py` + `alerts.py`（LINE Messaging API / Telegram，無憑證時 Mock）
- UI：`app.py`（Streamlit，禁止內嵌演算法邏輯）
- 規格：`specs/001` 為 as-built 基準；`002`（FVG 確認）已併入 main；
  `003`（短側）2026-07-12 重開為**台指期限定**、阻塞於期貨基礎建設；
  `007`（MSS fractal 反轉進場）長側已實作（US1–US3，SC-003 未達成如實記錄），短側待 003；
  `004~006` 見各 spec.md 狀態。新功能走 Spec Kit：
  `/speckit-specify` → `/speckit-plan` → `/speckit-tasks` → `/speckit-implement`
- 理論：`three_bands_theory.md`、`docs/ladder-optimization-research.md`（階梯優化研究，
  原 docx 之正式版）；歷史文件：`TrendPoint_OpenSpec.md`（勿當現行規格）

## 授權

本專案採 MPL-2.0（見 `LICENSE`）。新增原始碼檔案時在檔頭加上 MPL-2.0 標頭
（範例見既有核心 .py 檔頂部；若該檔型不適合放標頭則可省略）。

## 記憶

跨 session 教訓寫入 `~/.claude/projects/...TrendPoint/memory/`（格式與時機見
`.claude/docs/maintenance-protocol.md`）。repo 內制度檔與記憶庫二選一的判準：
與 repo 綁定的規則進 `.claude/docs/`，與使用者或環境綁定的事實進記憶庫。
