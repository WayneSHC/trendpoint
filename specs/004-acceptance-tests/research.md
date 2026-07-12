# Research: 驗收標準自動化測試套件（spec 004）

**Date**: 2026-07-12 | **Input**: 程式碼調查（Explore agent，逐檔核對 file:line）

本檔記錄 Phase 0 的五個關鍵決策。每項含 Decision / Rationale / Alternatives。

## R1. Parity 的可測定義：前綴一致性（prefix consistency）

**Decision**: US1 的「全量 vs 逐根增量」定義為：對截斷點 i，
`build_indicator_frame(df.iloc[:i])` 的最後一列，必須與
`build_indicator_frame(df)` 的第 i−1 列**零容差相等**
（`pd.testing.assert_series_equal`，比對欄位：atr、ladder、
upper/mid/lower_price、mss_signal、bos_signal、chandelier_long）。
截斷點取樣約 40 個（頭部密、尾部密、中段均勻），不跑全部 N 點。

**Rationale**: 調查確認系統**沒有**帶狀態的逐根增量引擎——
`monitor_signals.check_new_signals`（monitor_signals.py:105）的「即時」
就是對整段下載歷史全量重算後取最後一根已收盤 bar。因此生產環境中
「即時計算」與「回測計算」的唯一差異就是歷史前綴長度；前綴一致性
是這個差異的精確數學表述。它同時是看前偏誤的結構性防禦：
任何指標若使用第 i 根之後的資料，前綴計算必與全量不符。
取樣截斷點是因為全點驗證為 O(N²)；40 點已覆蓋 rolling 視窗邊界
（period=10/14/20/22 的前後）與尾部收斂行為。

**Alternatives considered**:
- *實作真正的增量引擎再比對*：範圍爆炸（等於重寫核心），且生產路徑本來就不是增量的——測一條不存在的路徑無意義。
- *全部 N 個截斷點*：10,000 根 × 全量重算 ≈ 分鐘級 CI 時間，違反測試可維護性；取樣已足以覆蓋邊界。

**已知限制（記入測試 docstring）**: Wilder ATR 與階梯遞迴皆從序列起點播種，
**不同起點**的兩段歷史（如監控端只抓 5 天）與全量回測的指標值本來就不會
逐位元相等——這是演算法性質，不是缺陷。Parity 保證的是「同起點、增長端點」
的一致性，與監控端實際運作方式相符。監控視窗長度是否足以讓 ATR 收斂，
屬於運營參數問題，不在本規格範圍。

## R2. 正典計算入口：抽出 `build_indicator_frame()`

**Decision**: 在 `ladder_system.py` 新增
`build_indicator_frame(df: pd.DataFrame, *, structure_period: int, atr_period: int = 14, ladder_k: float = 2.0, chandelier_period: int = 22, chandelier_multiplier: float = 3.0, include_regime: bool = True) -> pd.DataFrame`，
逐行搬移 `backtester.py:118-163` 的組裝邏輯（含內聯三關價與
yesterday_high/low groupby-shift 區塊），兩個呼叫端改用之。
欄位名以 backtester 版為正典（`mss_signal`/`bos_signal`）；
monitor 端改名對齊。

**Rationale**: 調查確認組裝邏輯在 backtester 與 monitor **重複內聯**
且已有實質分歧（欄名 `mss`/`bos` vs `mss_signal`/`bos_signal`、
monitor 缺 regime/chandelier/daily_open）——正是憲法 III 禁止的
silent drift。Parity 測試若無正典入口，只能複製第三份邏輯，
反而擴大 drift 面。

**迴歸閘門（憲法 I + 工作流程第 3 條）**: 重構 commit 必附前後回測對照
（同資料、同成本、同參數）：每檔標的交易筆數相同、每筆成交價相同、
組合總報酬相同。monitor 端另以「重構前後對同一固定 df 的判定結果相同」驗證。

**Alternatives considered**:
- *測試自行複製組裝邏輯*：drift 從兩份變三份，且測的不是生產路徑。
- *以 backtester.run_backtest 為受測入口*：混入部位管理與成本邏輯，無法對「指標值」做逐點斷言。

## R3. 延遲測試設計：中位數 + perf_counter，marker 隔離

**Decision**: `test_acceptance_latency.py` 以 `time.perf_counter` 量測
「對已備妥的 10,000 根 K 線 DataFrame 追加一根新 bar 後，
`build_indicator_frame()` + 讀取最後已收盤列判定訊號」的整段耗時；
跑 21 次取中位數，斷言 < 100ms。另量測監控實際視窗
（5 天 × 5 分 K ≈ 270 根）作第二個斷言（同預算）。
測試標記 `@pytest.mark.performance`，marker 在新增的 `pytest.ini` 註冊；
CI 主跑不加 `-m` 過濾（預設納入，spec FR-003），需要時可
`-m "not performance"` 排除。

**Rationale**: 生產的「新 bar 到達」路徑就是全量重算（R1），
所以量測全量重算即量測真實延遲。中位數 + 多次量測抗 CI 抖動
（spec Edge Case 明列）。10,000 根是 OpenSpec 的壓力上限情境，
270 根是實際運營情境，兩者都該在預算內。

**Alternatives considered**:
- *pytest-benchmark 外掛*：多一個依賴，spec Assumption 明言不引入新測試框架。
- *平均值*：對 GC/排程尖峰敏感，中位數穩健。

## R4. 離群值檢查是「先實作、後測試」——強化 `validate_data_contract`

**Decision**: 現行 `validate_data_contract`（data_ingestion.py:99）只擋
負值（`col < 0.0`），**價格為 0 與 1000 倍跳動目前都會通過**——
spec US3 場景 2 沒有現成受測行為，必須先補實作：
1. 價格欄位改為 `<= 0` 拒絕（開高低收；volume 仍允許 0）。
2. 新增相鄰收盤跳動檢查：`abs(close.pct_change()) > max_close_jump_ratio`
   即判離群，**raise `ValueError`（拒絕整批）並記 warning**，
   與既有契約「驗證失敗即拒絕」的語意一致（不做靜默過濾）。
3. 閾值 `max_close_jump_ratio`（預設 3.0，即單根 ±300%）進
   `config/config.yaml` 新 `data_quality` 區塊 + Pydantic 模型（憲法 V）。
   台股現貨有 10% 漲跌幅限制，3.0 對日線/分鐘線都極寬鬆，
   只攔截「千倍暴漲、歸零」級的資料錯誤，不會誤殺正常行情。

**Rationale**: spec 場景寫「驗證失敗（或離群列被過濾）」——二擇一時選
「拒絕」而非「過濾」：靜默修改輸入資料比拒絕更危險（被汙染的一批
資料裡，離群列之外的鄰近列也不可信），且拒絕讓上游 `fetch_stock_data`
的既有 try/except 降級路徑（回傳 None、跳過該標的）自然生效。

**Alternatives considered**:
- *z-score 統計離群偵測*：需要分布假設與更多參數，對「資料錯誤攔截」這個目的過度工程；跳動比率簡單、可解釋、可配置。
- *過濾離群列後放行*：靜默改資料，違反可重現性精神；拒絕 + 警告 + 上游降級更誠實。

## R5. 警告機制：`print` 遷移至 `logging`

**Decision**: `clean_kline_dataframe` 的缺漏填補警告（現為 `print`，
data_ingestion.py:87-95）與 R4 新增的離群警告，統一改用模組層
`logging.getLogger(__name__).warning(...)`。測試以 pytest 內建
`caplog` fixture 斷言警告內容（缺漏根數、離群位置）。

**Rationale**: spec US3 要求「發出警告紀錄」可被測試斷言；
`caplog` 是 pytest 原生機制，比攔截 stdout（`capsys`）語意正確且
不受其他 print 干擾。`print` → `logging.warning` 對 CLI 使用者
體感不變（預設仍輸出到 stderr/console）。

**Alternatives considered**:
- *保留 print + capsys 斷言*：能過測試，但「警告」與一般輸出不可區分，之後接告警系統時還是得改；一次到位。
- *warnings 模組*：語意是「程式碼使用警告」，資料品質事件屬運行日誌，logging 較正確。

## R6. Numba 兩模式一致性：沿用 CI 的 uninstall 策略

**Decision**: 不新增 fixture 切換機制。把 `test_acceptance_parity.py`
加入 `.github/workflows/tests.yml` 既有的「uninstall numba 後重跑」清單
（現清單：test_ladder_system.py、test_lookahead_bias.py）。

**Rationale**: 調查確認降級機制是 import-time no-op decorator
（ladder_system.py:23-33），**不是** `NUMBA_DISABLE_JIT` 環境變數——
同一個 Python 行程內無法乾淨切換，uninstall 重跑是現行且正確的策略。
Parity 測試在兩種模式下跑同一套零容差斷言，即滿足 spec Edge Case
「有／無 Numba 兩種模式皆通過」。

**Alternatives considered**:
- *測試內 monkeypatch jit*：jit 在 import 時已套用，事後 patch 無效；要生效得 reload 模組，脆弱且汙染其他測試。
