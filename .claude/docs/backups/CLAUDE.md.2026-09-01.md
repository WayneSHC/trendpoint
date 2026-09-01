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
   一律派 subagent，主對話只收結論與 `檔案:行號`。
   **讀之前先看規模**——常讀檔的行數如下（2026-08-06 實測，會漂移，取數量級即可）：

   | 讀法 | 檔案（行數） |
   |---|---|
   | **必先 Grep 定位、再 offset/limit 讀區段**（≥500 行） | `app.py` 992、`ladder_system.py` 809、`backtester.py` 793、`portfolio_backtester.py` 597、`config/config.py` 513 |
   | **可讀整檔，但別連讀多個**（200–500 行） | `run_b_segment.py` 499、`monitor_signals.py` 432、`walk_forward.py` 323、`docs/ladder-optimization-research.md` 240、`data_ingestion.py` 227、`trading_costs.py` 204 |
   | **整檔可讀**（<200 行） | `performance.py` 195、`optimizer.py` 186、`risk_gates.py` 126、`instruments.py` 116、`three_bands_theory.md` 82 |

   真正的制約是**單次工具輸出的大小**，不是檔案數或工具數——讀一個 800 行的檔
   與讀四個 200 行的檔成本相同。故「>2 個整檔就派 subagent」是行數的近似規則，
   遇到上表第一列的檔案，**讀一個就該考慮派**。
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

- 演算法核心：`ladder_system.py`（階梯系統）、`performance.py`（KPI）、
  `risk_gates.py`（spec 013 進場閘門：`DrawdownGate` 狀態機＋`settlement_days`
  純函式。**路徑相依風控，與無狀態指標層刻意分離**——它讀自己造成的權益，
  「rolling 要 .shift(1)」那條規則在此不適用，時序責任改由呼叫順序契約承擔：
  迴圈開頭讀 `blocked`、尾端 `update()`，搬動即看前偏誤）
- 回測：`backtester.py`（單標的，成本/sizing 走可插拔元件；spec 013 閘門接線點
  在 `if not pm.is_active` 區塊內、`if is_entry:` 之前，**只**改寫進場旗標——
  改成迴圈開頭 `continue` 會連出場與權益 append 一起跳過）、`trading_costs.py`
  （CostModel/PositionSizer 元件 + for_asset_class 工廠，spec 008b：現股 ad-valorem/
  期貨每口定額+保證金槓桿）、`portfolio_backtester.py`（**期貨護欄保留**，僅現貨）、
  `walk_forward.py`、`optimizer.py`、`monte_carlo.py`、`run_*.py` 為各入口。
  **B 段實測**走 `run_b_segment.py`（012/013 的預設關閉功能之啟用前後對照）——
  組態覆寫全在記憶體內、不寫回 config；判讀用**兩把尺**（訊號濾網看期望值/PF、
  風控閘門看 MDD/Calmar，情境表以 `kind` 欄宣告，勿用標籤字串比對）。
  無法本機取數時走 `.github/workflows/research_b_segment.yml`（手動觸發，
  產出 artifact）。**出網能力會隨 harness 版本漂移，別當常數**：曾實測 agent proxy
  擋掉 yfinance 與 TAIFEX（403），但 2026-08-07 於本機 Claude Code 實測**兩者皆通**
  （TAIFEX 三商品回填、yfinance 五檔現貨全部成功）。故先試跑一次再決定要不要繞
  GitHub runner，勿因這行字直接放棄本機取數
- 資料：`instruments.py`（Instrument 資產類別抽象 + registry，spec 008a）→
  `data_sources/`（可插拔來源 adapter：yfinance/csv/mock + **taifex 真源/finmind 驗證源**
  （spec 010）；`rollover.py` 連續月引擎——量最大月轉倉 + back-adjust）→
  `data_ingestion.py` → SQLite `trendpoint.db`（gitignored）；表名一律經
  `db_security.table_name_for`（equity→`stock_*`、futures→`fut_*`、raw 層→`fut_*_raw_*`）；
  `verify_futures_data.py` 雙源交叉驗證（哨兵，需 FINMIND_TOKEN 環境變數；
  **逐 taifex instrument 各驗一次**——只驗第一個會讓未驗商品被說成驗過了）；
  期貨監控取數＝讀庫＋當日端點（**禁**輪詢中呼叫重量 fetch()）；`data/*.csv` 為快取。
  **現貨只入日線**：`stock_*_5m` 曾被寫入但從未被任何程式讀取，已停止產生
  （監控端 5 分線一律現抓——繞經 DB 不會少一次下載，5 分線本質是盤中即時資料）。
  `run_ingestion.py --equity-only` 供排程監控預熱日線表（`alert_scheduler.yml`
  每 30 分鐘先跑它再跑監控）；**該旗標不可拿掉**——TAIFEX 表空時會回填 1998 年起
  全歷史（每請求節流 2 秒），放進 30 分鐘排程會爆。期貨連續表仍須本機跑完整 ingestion
- 通知：`monitor_signals.py` + `alerts.py`（LINE Messaging API / Telegram，無憑證時 Mock）。
  **監控與回測刻意不同源，這不是缺陷**：現貨監控走 5 分線（yfinance 現抓 5 天）＋
  硬編碼結構參數（`structure_period=10`、`use_fvg=True`、`include_regime=False`），
  回測/消融/UI 一律走日線＋config 參數；期貨兩端同為日線。故監控定位是
  **盤中提示，非回測驗證過的訊號**（推播訊息已附註記）。
  「對齊時框」在現行資料源下不可行——yfinance 的 5m 只給 5 天（約 270 根），
  跑不出統計意義；真要做需先換資料源。詳見
  `docs/reviews/2026-07-30-tradingview-mcp-workflow-review.md`。
  **均線觸價通知**（spec 014）：`ma_lines.py`（純函式：均線計算＋向下穿越判定，
  刻意與 `ladder_system.py` 分離——它是通知用參考價位，**不進訊號或回測路徑**）。
  時基刻意混合：均線取 DB 日線（年線需 240 根日線，5 分線算不出來）、
  比較價取 5 分線已收盤棒；兩條資料路徑**並存**，改動任一端前先讀
  `specs/014-ma-touch-alerts/research.md` D1。總開關 `alerts.ma_alerts_enabled`
  預設關閉。此案是 `stock_*_daily` 在監控端的第一個消費者。
  **訊號事後表現追蹤**（spec 015 A 段）：`alert_outcomes.py`（觀察層純函式＋
  JSONL 儲存層）＋ `alert_log/YYYY-MM.jsonl`（**進版本庫**，不可再生成之原始觀察，
  故不 gitignore）。偵測當下即落一列（**在去重判定之前**——沿用
  `mark_alert_as_sent` 的時機會讓推播失敗的訊號永不被記錄），另以 `notified`
  欄分離「訊號成立」與「使用者收到」；事後回填 T+1/T+3/T+5 日線收盤報酬。
  三條紅線：(1) **產出不是策略績效**——無成本、無出場規則、未經樣本外驗證，
  UI 禁與回測 KPI 並列；(2) **不新增 SQLite 表**，`db_security` 的
  `TABLE_NAME_PATTERN` 不得為本案放寬（出現此念頭即代表偏離設計）；
  (3) **永不進訊號鏈**——它持有告警發生之後的價格，任何回測/訊號模組 import
  即未來函數入口，由 `tests/test_alert_outcomes.py` 靜態零引用檢查焊死。
  監控端結構參數的單一來源為 `monitor_signals.MONITOR_STRUCTURE_PARAMS`
  （同時餵 `build_indicator_frame` 與參數識別值，分兩份會讓紀錄悄悄說謊）。
  總開關 `alerts.outcome_tracking.enabled` 預設關閉
- UI：`app.py`（Streamlit，禁止內嵌演算法邏輯）
- 規格：`specs/001` 為 as-built 基準；`002`（FVG 確認）已併入 main；
  `007`（MSS fractal 反轉進場）已併入 main（SC-003 未達成如實記錄），短腿由 003 解封；
  `008a`（資料層）+ `008b`（期貨成本/口數，`specs/009-taifex-cost-model`）已併入 main；
  `003`（台指期做空）已併入 main——期貨單標的**多空**回測
  （`enable_short` 預設關、現貨結構硬邊界、鏡像對稱測試）；組合路徑護欄保留（僅現貨）；
  `010`（真實台指期資料源，`specs/010-taifex-real-data`）已併入 main——台指類
  **三商品全接 TAIFEX 官方**（大台 TXF/小台 MTX/微台 TMF，全歷史回填 + 每日增量）、
  FinMind 交叉驗證逐商品各跑一次。三者同標的指數、**訊號序列本質相同**，
  差別只在合約規模（每點 200/50/10）→ 成本佔比與口數量化粒度；
  勿把三份回測結果當成三個獨立標的的樣本數。回填起始日逐商品設於
  `data.futures_source.backfill_start_overrides`（上市日不同：1998/2001/2024，
  共用起點會空轉數百個月請求）；
  `011`（未調整參考價，`specs/011-unadjusted-sizing-price`）解決 010 的 sizing
  失真已知限制——**期貨連續表帶兩組價格**：調整後 OHLC 供訊號與每點損益、
  `unadj_*` 四欄（平移前擷取的原始近月價）供口數/保證金/期交稅等名目值計算。
  凡「價位 × 乘數」型計算一律用未調整價；**禁止**由「調整後 − 位移量」回推
  （位移量是未來轉倉的函數且非截斷不變）。期貨資料缺 `unadj_*` 時回測硬失敗
  不 fallback，故所有期貨來源（含 mock）皆須產出該欄位；
  `014`（均線觸價通知，`specs/014-ma-touch-alerts`）已實作——月/季/半年/年線
  向下穿越推播（總開關預設關閉）＋儀表板均線現況表；通知層功能、不進訊號路徑，
  故無回測對照需求。`015`（推播訊號的事後表現追蹤，
  `specs/015-alert-outcome-tracking`）**A 段已實作**——觀察層，總開關預設關閉，
  關閉時逐筆逐則與實作前相同（基準凍結於 `tests/fixtures_015_baseline_alerts.json`）；
  解開 2026-07-30 審查的死結（盤中推播**回溯**驗證不可行，但**前瞻累積**可行）。
  B 段（5 分粒度短視窗）未做。**在 SC-022 實跑量測告警頻率之前，不得宣稱本案
  「證明了訊號有效」**——樣本頻率未經量化是本案已知的最大不確定性。`013`（進場閘門：回撤上限＋結算日封鎖，
  `specs/013-entry-gate-risk-limits`）**A 段已實作**——兩道閘門**預設關閉**，
  關閉時逐筆、逐根、逐欄與實作前相同（基準凍結於 `tests/fixtures/013_baseline_*`）；
  **B 段已實測**（run 31138969771，2026-08-07）：回撤閘門在七個標的**全部封鎖 0 根**
  ——這是**未觸發、無對照數據**，不是「無效」（結構性原因：SC-015 的門檻取自重抽
  分布深尾，必然深於單一歷史路徑的 MDD，故被正確校準的門檻在校準它的序列上本就
  幾乎不該觸發）；結算日閘門在 TXF **MDD 加深 1.37pp、Calmar 惡化一倍**。
  兩道閘門**維持關閉且不再調參**。`012`（BOS 續勢進場的量能確認，`specs/012-bos-volume-confirmation`）
  **A 段已實作**——`ladder_system.calculate_volume_confirmation` 純函式 +
  `bos_volume_ok` 條件輸出欄，**預設關閉**。作用於**進場判定層**而非訊號層
  （訊號層抑制 BOS 會讓 `~bos` 互斥條件放行原本被排除的 MSS，等於改寫訊號定義）；
  **只接續勢分支**，MSS 反轉分支不傳（該路徑已內建自己的位移量能確認，
  再套一次即雙重套用）。**B 段已實測**（同一輪 run）：SC-010 明文要求的兩個標的
  （0050／TXF）**都惡化**，且樣本最多的 TXF 期望值 −1.560pp、PF 0.76→0.37；
  四個「改善」的標的中三個是把交易砍到 2~5 筆換來的（兩個因此勝率 100%、PF=inf）。
  **維持關閉且不再調參**——結論即 `run_ablation.py` 早已載明的失效模式；
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
