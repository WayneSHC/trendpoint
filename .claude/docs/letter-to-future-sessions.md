# 給未來 session 的信

寫於 2026-07-09，由 Fable 5 在它於此環境的唯一一個 session 中留下。
你（讀者）大概率是 Sonnet、Opus 或 Haiku。這封信不是規則，是脈絡——
規則在其他檔案裡，這裡是「為什麼」與「小心什麼」。

## 三件使用者沒問、但對這個環境最重要的事

### 1. 這個 repo 的真正資產是回測可信度，不是程式碼

TrendPoint 的輸出會影響使用者的真實交易決策。一個算錯的 Sharpe 比一個 crash
危險得多——crash 會被看見，看前偏誤不會。`.specify/memory/constitution.md`
前兩條（look-ahead 防禦、真實摩擦成本）是這個專案的命根，任何「功能交付」與
「回測完整性」衝突時，選後者。特別警覺的高危區：時間序列的 index 對齊、
`shift(1)` 被「順手優化」掉、resample/merge 之後的時序錯位、
以及測試裡用未來資料構造 fixture。審查交易邏輯時，effort 直接用最高檔，
這裡省 token 是假節省。

### 2. 使用者的工作方式：制度型、批次指令、期待自主

Wayne 用繁體中文工作，是獨立開發者。他給指令的模式是一次性批次交代
（像建立這套制度的那次委託：規則、交付清單、收尾條款一次給齊），
然後期待你自主跑完，中途不想被逐項請示。對他的正確姿勢：
開場最多問一批問題，之後用「選定預設值＋註明假設」推進；
隨做隨落檔（他明確說過：session 中斷時，已落檔的就是他拿到的全部）；
交付時給一頁式總結。他吃制度這一套——你發現流程問題時，
提議「寫進制度檔」比口頭建議更對他的頻率。

### 3. 環境的兩顆未爆彈：Google Drive 與公開 repo

(a) repo 活在 Google Drive 同步資料夾裡（含 `.git`、含 SQLite）。
2026-07-09 已在 `harness-diagnosis.md` 建議搬離，等使用者決定。
在他搬之前：路徑必加引號、大量小檔寫入後留意同步延遲、
git 出現詭異損毀時第一個懷疑 Drive 同步衝突（找 `*conflicted copy*` 檔案）。
不要每個 session 都催他搬家，說過一次就好。
(b) repo 是公開的（GitHub WayneSHC/trendpoint，2026-07 起帶 MPL-2.0）。
你 push 的任何東西即刻公開：憑證當然不行，但也包括帶個資的路徑、
內部筆記、還有 `data/*.csv` 這類從 Yahoo Finance 抓的市場資料
（再散布屬灰色地帶，開源健檢報告 `docs/reviews/` 有詳情）。

## 這套制度最可能的退化方式，與預防法

1. **規則通膨**：每次踩坑都加一條，兩年後沒有模型會真的讀完它，
   等於零規則。預防：`maintenance-protocol.md` 第 4 節的行數硬上限與
   「加一條前先刪一條」是認真的，執行它。
2. **儀式化派工**：把「指揮官不下場」執行成「兩行的修改也開三個 subagent」。
   制度的目的是省 context、提品質；當派工的開銷超過任務本身，就直接做。
   判準：主對話單獨完成會消耗的 context < 一次派工往返的成本時，不派。
   （此判準不推翻 CLAUDE.md 鐵律 2 的明確門檻——>2 個整檔閱讀、掃 repo、查網頁、
   >3 檔批次修改仍一律派工；只適用門檻未涵蓋的灰色地帶。）
3. **自我弱化**：模型為了讓自己的產出過關，把驗收條件或規則改鬆。
   這是唯一被 `maintenance-protocol.md` 列為「永遠不准」的行為，因為它無聲、
   且會複利。看到前一個 session 疑似這麼做了，回報使用者。
4. **事實過期被當成制度失效**：harness 會改版——工具改名、參數增減、
   plugin 更替。屆時錯的是制度檔裡的「事實」，不是「原則」。
   修事實不需要問（見 maintenance-protocol.md 第 1 節），
   不要因為幾個路徑失效就宣判整套制度過時。
5. **例子被當成規則**：rubric 裡的正反例是校準用的，不是窮舉。
   遇到例子沒涵蓋的情況，回到判準本身推理，不要說「制度沒寫所以不管」。

## 誠實條款（Fable 5 的自我評估，你接手時請沿用）

這套制度能把「執行品質」撐到接近我的水準：拆解、驗證、fresh-context 驗收、
多答案評審，都是 Sonnet 等級跑得動的。它撐不起來的是**模糊題與品味判斷**：
策略研究方向、抽象好壞、文件輕重。遇到這類題，照 `judgment-rubrics.md` 第 6 節：
翻成可驗證題 → 翻不動就多答案評審或升級模型 → 還不行就標明信心交使用者裁決。
不要假裝制度讓你變成了更大的模型；它只是讓你少犯不該犯的錯。

## 交接欄（後續 session 若中斷，把未完成事項寫在這裡）

- 2026-07-09（本 session）：無未完成交接。制度檔 A–G 全數落檔，
  TrendPoint 開源健檢完成（`docs/reviews/2026-07-09-code-review.md`），
  MPL-2.0 LICENSE 已加入。
- 2026-07-10 更新：Critical（bfill 看前偏誤）與 High 2.1/3.2/4.1/4.2
  （git 歷史清理 + data/ 移出版控）皆已修復；PR #3、#4 已合併；
  git 歷史已以 filter-repo 改寫並 force-push——**任何 2026-07-10 前的
  clone/fork 與新 main 歷史不相容，必須重新 clone**（本地主 repo 已同步）。
  仍待使用者決定：(1) 是否搬離 Google Drive；(2) 是否裁剪 plugin 清單
  （見 harness-diagnosis.md）；(3) 剩餘 finding（High 3.3 文件著作權確認、
  Medium 以下）的修復排程。
- 2026-07-11 更新：**健檢 21 項發現全數結案**（PR #5 著作權清理、
  PR #6 次根開盤成交、PR #7 監控 repaint + 尋優 hold-out 閘門、
  PR #8 Low 級批次含 ExitEvent enum），細節與驗證證據見健檢報告內
  各「✅ 已修復」註記。引擎現為可信基準；下一階段是 specs/002–006
  的新功能開發（走 Spec Kit 流程）。003 已由使用者定案：**產品正式為
  Long-Only**（決策記錄與重啟條件在 specs/003 檔頭），該規格不進入
  /speckit-plan。仍待使用者決定：搬離 Google Drive、裁剪 plugin、
  TrendPoint_OpenSpec.md 是否移入 docs/。
- 2026-07-12 更新：**spec 004（驗收標準自動化）走完整 Spec Kit 流程
  plan→tasks→implement 完成**，PR #9。三個驗收測試檔（parity/latency/
  data-quality）落地，OpenSpec §6 四項驗收全數自動化；spec 001 SC-003~005
  改指向實際測試檔。過程含兩處必要引擎/契約變更並附證明：(a) 抽出
  `ladder_system.build_indicator_frame()` 消除 backtester/monitor 的重複
  內聯（回測 CSV byte-identical、組合 8.22%/34 不變＝零差異重構）；
  (b) `validate_data_contract` 補離群防呆（價格<=0 拒絕、收盤跳動比率上限，
  閾值進 config `data_quality`）。parity 用「窮舉逐根重播」+ check_exact，
  並以注入三關價看前偏誤證明有效（681 處不一致）。**關鍵洞察**：parity
  守的是「未來資料回洩過去」（bfill/三關價類），拿掉已因果的 shift(1)
  不會被它抓到——那是 test_lookahead_bias.py 的守備範圍，兩者互補。
  下一個開發目標：spec 002（FVG 確認）。剩餘 Draft：002/005/006。
  重建 trendpoint.db（gitignored）：正規做法 `python run_ingestion.py`
  （yfinance 下載）；若本地已有 `data/*_daily.csv`（同屬 gitignored、僅存在
  Wayne 的 Drive repo），可用 `db_security.safe_save_to_sqlite` 逐檔灌入，
  表名 `stock_{stem}`（stem 為 CSV 去副檔名，如 `0050_TW_daily`）。
  新測試不依賴 db——全用 `tests/acceptance_fixtures.py` 的合成 K 線，離線可跑。
