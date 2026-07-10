# 多空階梯系統優化與實戰策略研究

> 本文件為 TrendPoint 多空階梯系統之設計研究筆記。初稿由作者綜合自身研究、
> 與 LLM AI 研究工具共同產出（2026-05），經作者審閱編修；文中引用之技術指標
> （ATR、Chandelier Exit、三關價／Fibonacci Pivot Bands、MSS/BOS 市場結構概念）
> 皆為公開交易方法，公式為自行撰寫之文字表達。依本 repo 之 MPL-2.0 授權散布。

## 針對多空階梯系統（Ladder System）之邏輯強健性、運算效率與進場精確化之深度優化研究報告

在當前高度競爭的演算法交易市場中，傳統的趨勢追蹤系統面臨著假突破頻發與獲利回吐嚴重的雙重挑戰。多空階梯系統（Ladder System）作為一種結構化的趨勢跟蹤模型，其核心優勢在於能將動態的價格波動轉化為具備支撐與壓力屬性的階梯狀水平。然而，原本基於 Python 迴圈與簡單固定閾值的偵測邏輯（detect_ladder），在面對 1 分鐘級別高頻數據或台指期（TX）等高波動品種時，顯現出運算滯後與訊號過濾不足的問題。

本報告旨在透過引入「結構破壞（MSS）」、平均真實波幅（ATR）動態調整以及高效的 Pandas 向量化運算，對多空階梯系統進行深度的實戰級改良。分析顯示，這種從「價格驅動」轉向「結構與波動驅動」的邏輯升級，不僅符合制度化交易（Institutional Trading）的風格，更能有效提升盈虧比與資金周轉率。

## 市場結構動力學：從價格追逐到結構破壞（MSS）的範式轉移

量化交易者常面臨的一個核心問題是：如何區分趨勢的健康回調與真正的趨勢反轉。原本的階梯系統僅依靠價格是否突破前一階梯來判斷多空，這在盤整區間（Consolidation）極易引發連續止損。引入「結構破壞（Market Structure Shift, MSS）」概念，正是為了解決這一邏輯漏洞。

### 結構破壞（MSS）與結構連續（BOS）的辨識邏輯

在內盤結構（Internal Structure）分析中，趨勢的變更並非瞬間發生，而是伴隨著市場參與者偏見的移位。結構破壞（MSS）通常發生在市場從分發（Distribution）轉向累積（Accumulation）的轉折點 1。在看漲趨勢中，市場持續創造更高的高點（HH）與更高的低點（HL）。一旦價格以強勁的動能（Displacement）跌破前一個關鍵的 HL，即產生了看跌 MSS，這標誌著原始趨勢的結構完整性遭到破壞 1。

與之相對的是結構連續（Break of Structure, BOS）。BOS 是趨勢持續的確認訊號，發生在價格突破既定趨勢方向上的外部波段高點（Uptrend）或低點（Downtrend）時 1。優化後的階梯系統應將 MSS 視為「偏見轉向」的預警，而將 BOS 視為「階梯確認」的信號。

下表對比了 MSS 與 BOS 在多空階梯系統中的功能差異：

| 功能維度 | 結構破壞 (MSS) | 結構連續 (BOS) |
| --- | --- | --- |
| 定義 | 價格跌破反向關鍵波段點 | 價格突破同向波段點 |
| 趨勢含義 | 潛在反轉、偏見移位 | 趨勢加強、慣性延續 |
| 動量要求 | 必須具備強力位移 (Displacement) | 常態性突破即可 |
| 主要功能 | 預警止損、尋找反手進場 | 階梯上移、部位加碼 |
| 心理特徵 | 市場引入懷疑 1 | 市場強化信心 1 |
| 伴隨現象 | 公平價值缺口 (FVG) | 持續的趨勢波浪 |

### 位移（Displacement）與公平價值缺口（FVG）的量化

MSS 的有效性高度依賴於「位移」的程度。在程式化邏輯中，位移不應僅被視為簡單的價格跨度，而應定義為單根或多根 K 線以極高的成交量與波動率貫穿結構位 3。這種現象往往會留下公平價值缺口（Fair Value Gap, FVG），即三根 K 線結構中，第一根的高點與第三根的低點（看漲情形）之間未被覆蓋的區域 1。

在優化後的 detect_ladder 中，增加 FVG 的偵測逻辑可顯著提升系統的強健性。當 MSS 伴隨著 FVG 出現時，代表機構資金正在進行大規模的部位重組，這時產生的階梯轉向訊號具備極高的成功率 1。

## 波動率錨定：ATR 與台指期三關價的實戰融合

實戰中，進場點的精確化不能僅依賴於靜態價格。不同市場環境下的「階梯寬度」必須根據波動率進行動態調整，這正是引入平均真實波幅（ATR）的初衷。

### ATR 動態階梯間距的設計

ATR（Average True Range）是衡量市場波動的標準工具，透過計算當前 K 線的高低差以及與前一收盤價的差距，能客觀反映市場的「真實呼吸空間」 6。在 Ladder System 中，階梯的觸發閾值應設定為 $k \times ATR$。

$$TR = \max(High - Low,\ |High - Close_{prev}|,\ |Low - Close_{prev}|)$$

$$ATR_n = \frac{(n-1) \times ATR_{prev} + TR}{n}$$

當市場處於高 ATR 階段（如開盤前 30 分鐘），階梯間距應自動放寬，以避免被隨機噪音掃損；在低 ATR 階段（如中盤整理），間距應縮小以捕捉微小的結構突破 6。

### 台指期「三關價」作為全域濾網

針對台指期（TX）交易，單純的局部階梯可能忽視了全域的支撐壓力。將傳統的「三關價」邏輯引入系統，可以為 Ladder 提供更高層級的方向指引 9。

三關價計算公式如下 9：

- 中關價 (Middle Price):

  $$\text{中關價} = \frac{\text{昨日最高} + \text{昨日最低}}{2}$$

- 上關價 (Upper Price):

  $$\text{上關價} = \text{昨日最低} + (\text{昨日最高} - \text{昨日最低}) \times 1.382$$

- 下關價 (Lower Price):

  $$\text{下關價} = \text{昨日最高} - (\text{昨日最高} - \text{昨日最低}) \times 1.382$$

在系統優化中，中關價應被視為當日的「多空分水嶺」 9。若當前價格位於中關價之上，Ladder 系統應僅執行多頭階梯邏輯，並過濾掉大部分空頭訊號；反之亦然。而上關價與下關價則作為趨勢盤（Trend Market）的引爆點，當價格突破上關價，代表市場進入極強勢區域，此時應降低 ATR 乘數，採取更激進的追價策略 9。

下表展示了三關價與 Ladder 系統整合後的策略矩陣：

| 價格位置 | 市場偏見 | Ladder 策略行為 | 風險控管建議 |
| --- | --- | --- | --- |
| 高於上關價 | 強勢多頭 | 激進多頭、ATR 乘數縮小 | 防止超漲回吐，使用緊湊止損 |
| 中關與上關之間 | 震盪偏多 | 標準多頭階梯 | 觀察 BOS 是否持續形成 |
| 中關與下關之間 | 震盪偏空 | 標準空頭階梯 | 觀察是否存在 MSS 反轉跡象 |
| 低於下關價 | 強勢空頭 | 激進空頭、ATR 乘數縮小 | 防止超跌反彈，鎖定利潤 |

## 運算效率優化：Pandas 向量化與 Numba 高效執行

原本使用迴圈遍歷 DataFrame 的 detect_ladder 函數在處理大規模數據時效率低下。Python 的原生迴圈在處理百萬級別的 K 線時會導致顯著的 CPU 延遲，這在需要即時反應的期貨交易中是不可接受的 10。

### 向量化運算的技術優勢

向量化（Vectorization）的核心思想是利用 Pandas 與 NumPy 的底層 C 語言實現，一次性對整個陣列進行批量運算 10。例如，計算移動平均或滾動最高價（Rolling High），Pandas 的 .rolling() 函數比迴圈快上數百倍。

在優化後的系統中，階梯的計算應盡量轉化為數值平移（Shifting）與邏輯掩碼（Boolean Masking）。例如，偵測 BOS 的向量化偽代碼如下：

```python
# 向量化偵測 BOS (突破前 N 週期最高價)
df['prev_high'] = df['high'].rolling(window=period).max().shift(1)
df = (df['close'] > df['prev_high']) & (df['volume'] > df['volume'].rolling(20).mean())
```

這種方式完全消除了 for 迴圈，使得計算時間與數據量呈線性關係而非指數關係 13。

### Numba JIT 與自定義運算加速

對於某些具備強時序依賴性（Recursive Dependency）的邏輯，如動態調整的止損價或累積 VWAP，純粹的向量化有時難以實現。此時應引入 Numba 的 @jit 裝飾器 15。Numba 能夠在運行時將 Python 函數編譯為機器碼，執行速度可媲美 C++。

根據效能基準測試，計算 VWAP 指標時的不同方法耗時如下表 17：

| 運算方法 | 每 10,000 筆數據耗時 (µs) | 效能提升倍率 |
| --- | --- | --- |
| 純 Python 迴圈 | 5,200 | 1.0x |
| Pandas.apply() | 2,800 | 1.9x |
| Pandas 標準向量化 | 829 | 6.3x |
| NumPy 向量化 | 165 | 31.5x |
| Numba @jit 加速 | 87 | 59.8x |

因此，優化後的 Ladder 系統應將計算密集型的結構偵測邏輯封裝於 @jit(nopython=True) 函數中，以確保在大數據回測與實盤交易中的極速響應 16。

## 進場點精確化：蠟燭顏色確認與 VWAP 定價參考

在確定了結構位與波動率之後，最後的「臨門一腳」在於進場時機的細化。結合 VWAP 與蠟燭行為（Candle Action）是過濾假訊號的有效手段 19。

### VWAP 作為機構均價濾網

成交量加權平均價（VWAP）被廣泛認為是機構投資者的「公平定價」參考線 20。在優化後的 Ladder 系統中，多頭進場必須滿足價格高於 VWAP，且 VWAP 呈現上升趨勢。這確保了我們是在買盤力量佔優的情況下順勢操作 19。

### 蠟燭顏色與開盤價的二次確認

針對台指期等日次交易（Intraday）強烈的市場，當日的開盤價（Daily Open）具有極高的參考價值。優化後的進場邏輯應包含以下多重確認 19：

- 結構端： 出現看漲 MSS 或 BOS 1。
- 動能端： 訊號 K 線必須為綠色（陽線），且收盤價高於開盤價 19。
- 趨勢端： 價格同時處於當日開盤價與 VWAP 之上 19。
- 波動端： 突破當下的 K 線振幅大於 1.2 倍 ATR，確認具備有效的位移 3。

這種多維度的精確化設計，能有效排除盤整期無意義的階梯跨越，將勝率從隨機分佈提升至具備統計學意義的優勢區間 11。

## 止盈（Take Profit）邏輯的深度擴充

針對用戶提出的「止盈邏輯擴充」需求，本研究認為這是提升系統期望值（Expectancy）的關鍵環節。一個完善的止盈體系應由「分批退出」、「動態跟蹤」與「時間過濾」三部分組成 22。

### 分批規模化退出（Scaling Out）策略

在趨勢交易中，單一目標止盈常導致錯失隨後的大行情。建議採用 2 至 3 階段的止盈邏輯 22：

- 階段 1 (初始目標)： 當獲利達到 $1.5 \times ATR$ 時，平倉 50% 的部位。這能迅速收回初始風險，並心理上支撐交易者持有剩餘倉位 22。
- 階段 2 (動態保護)： 第一階段止盈後，將剩餘部位的止損移至進場位（Breakeven），實現零風險持倉 22。
- 階段 3 (趨勢跟蹤)： 剩餘部位不設固定目標，而是使用「吊燈式止損（Chandelier Exit）」進行滾動止盈，直到趨勢結構出現 MSS 反轉訊號 24。

### 吊燈式止盈（Chandelier Exit）的實踐

吊燈式止盈是一種基於波動率的動態保護機制，其設計邏輯是給予趨勢合理的波動空間，但在異常回撤時果斷離場 7。

對於多頭部位：

$$\text{Chandelier Exit (Long)} = \text{RollingMax}(High, n) - (ATR_n \times \text{Multiplier})$$

其中 Multiplier 的選擇至關重要 24：

- 一般市場： 3.0x ATR（平衡盈虧比與勝率） 25。
- 高波動/科技股： 4.0x - 5.0x ATR（避免過早被洗盤出場） 25。
- 低波動/穩定品種： 2.0x - 2.5x ATR（及時鎖定利潤） 25。

### 時間與量價過濾器的引入

除了價格空間外，止盈邏輯還應包含以下維度：

- 時間止盈（Time-Based Exit）： 若部位在進場後 $N$ 根 K 線內未能脫離成本區，或在當日收盤前 15 分鐘強制平倉，以規避不確定的隔夜跳空風險 22。
- 量價背離止盈： 若價格創新高（BOS），但成交量與 RSI 動能未能同步創新高，則觸發警戒，主動平倉 25% 倉位 22。

下表總結了擴充後的止盈管理架構：

| 止盈組件 | 觸發條件 | 執行行為 | 核心目的 |
| --- | --- | --- | --- |
| 目標 A | 獲利達 $1.5 \times ATR$ | 平倉 50% | 回收初始風險、鎖定基本收益 |
| 保本機制 | 目標 A 達成後 | 止損移至 Entry | 實現交易零風險化 |
| 吊燈跟蹤 | 基於 Chandelier Exit 公式 | 動態上移止損線 | 讓利潤奔跑，捕捉波段大行情 |
| 結構反轉 | 出現反向 MSS | 全數清倉 | 在趨勢確立結束時離場 |
| 時間限制 | 收盤前 15 分鐘 | 強制清倉 | 規避期貨結算與隔夜風險 |

## 邏輯強健性防禦：避免向量化回測中的看前偏誤

在進行上述優化時，量化開發者極易陷入「看前偏誤（Look-Ahead Bias）」的陷阱，特別是在使用向量化運算時 13。若計算階梯的邏輯包含了未來的信息，回測報告將會出現不真實的爆炸性增長。

### 嚴格的時序移位規範

在計算任何技術指標或結構位時，必須確保數據的獲取在決策時點之前。例如，當前的 BOS 判斷必須使用「前一根」K 線的最高價 13：

```python
# 錯誤示範 (直接用 current high，涉及 look-ahead)
signal = df['close'] > df['high'].rolling(20).max()

# 正確示範 (使用 shift(1) 模擬實際交易場景)
signal = df['close'] > df['high'].rolling(20).max().shift(1)
```

此外，回測引擎應考慮滑點（Slippage）與手續費的衝擊。研究指出，一個在 0% 手續費下表現優異的 VWAP 策略，在加入 0.1% 的交易成本後，總報酬率可能從 713% 瞬間跌至 -97% 19。因此，在優化 Ladder 系統的同時，必須確保系統的盈虧比足以覆蓋高頻階梯切換帶來的交易摩擦成本 19。

## 結論與實戰路線圖

透過對多空階梯系統（Ladder System）在邏輯強健性、運算效率及進場精確化三個維度的深度改良，我們構建了一個更符合制度化資金行為的交易架構。

主要優化結論如下：

- 結構化思維： 引入 MSS 與 BOS 代替單純的價格突破，使系統具備區分趨勢反轉與持續的能力 1。
- 波動適應性： ATR 的引入使階梯間距動態化，顯著降低了在不同市場環境下的「偽訊號」頻率 6。
- 計算極速化： 全面實施 Pandas 向量化並針對核心迴圈引入 Numba JIT，確保了 1 分鐘級別數據處理的即時性 10。
- 進場精確化： 結合三關價全域濾網、VWAP 均價參考與蠟燭顏色確認，實現了高概率的進場點選擇 9。
- 止盈體系擴充： 建議採納分批獲利（1.5x ATR）與動態跟蹤（3.0x ATR 吊燈止損）相結合的複式管理模型，這是提升策略穩定性的核心 22。

未來在實盤佈署中，應持續關注台指期夜盤與日盤波動率的差異，並根據不同交易時段適時調整 ATR 的週期參數與三關價的加權權重 9。這套優化方案不僅在邏輯上具備強健性，在技術執行上也展現了現代量化開發的最佳實踐。

## 引用的著作

- Why MSS vs BOS matters in market structure - Equiti, 檢索日期：5月 11, 2026， https://www.equiti.com/sc-en/news/trading-ideas/mss-vs-bos-the-ultimate-guide-to-mastering-market-structure/
- Break of Structure (BOS) Explained - Flux Charts, 檢索日期：5月 11, 2026， https://www.fluxcharts.com/articles/break-of-structure-bos-explained
- Understanding Market Structure and Market Structure Shift (MSS) - ATAS, 檢索日期：5月 11, 2026， https://atas.net/blog/understanding-market-structure-and-market-structure-shift-mss/
- Market Structure Shifts (MSS) in ICT Trading - LuxAlgo, 檢索日期：5月 11, 2026， https://www.luxalgo.com/blog/market-structure-shifts-mss-in-ict-trading/
- Break of Structure (BOS) - StrategyQuant, 檢索日期：5月 11, 2026， https://strategyquant.com/codebase/break-of-structure-bos/
- Tips for Using the Average True Range (ATR) Indicator in Your Trading | Technical Analysis, 檢索日期：5月 11, 2026， https://www.oanda.com/us-en/trade-tap-blog/analysis/technical/how-to-use-average-true-range-atr/
- Chandelier Exit Strategy: A Trader's Guide - QuantifiedStrategies.com, 檢索日期：5月 11, 2026， https://www.quantifiedstrategies.com/chandelier-exit-strategy/
- Average True Range (ATR) Indicator & Strategies - AvaTrade, 檢索日期：5月 11, 2026， https://www.avatrade.com/education/technical-analysis-indicators-strategies/atr-indicator-strategies
- 三關價操作法讓你1次就學會！ - winsmart.tw, 檢索日期：5月 11, 2026， https://winsmart.tw/en/online_teaching/%E4%B8%89%E9%97%9C%E5%83%B9/
- Fast, Flexible, Easy and Intuitive: How to Speed Up Your pandas Projects - Real Python, 檢索日期：5月 11, 2026， https://realpython.com/fast-flexible-pandas/
- Fast Stock Data Preprocessing for Day Trading - Kaggle, 檢索日期：5月 11, 2026， https://www.kaggle.com/code/niladmirari/fast-stock-data-preprocessing-for-day-trading
- Pandas for Finance: Analyze Stock Data Like a Pro | by Bhagya Rana | Medium, 檢索日期：5月 11, 2026， https://medium.com/@bhagyarana80/pandas-for-finance-analyze-stock-data-like-a-pro-15149474603b
- A Practical Breakdown of Vector-Based vs. Event-Based Backtesting - Interactive Brokers, 檢索日期：5月 11, 2026， https://www.interactivebrokers.com/campus/ibkr-quant-news/a-practical-breakdown-of-vector-based-vs-event-based-backtesting/
- backtest_tutorial/Vectorized_Backtest_Tutorial.ipynb at main - GitHub, 檢索日期：5月 11, 2026， https://github.com/hudson-and-thames/backtest_tutorial/blob/main/Vectorized_Backtest_Tutorial.ipynb
- Custom Statistical Functions with Numba - Statology, 檢索日期：5月 11, 2026， https://www.statology.org/custom-statistical-functions-numba/
- A quick tester of trading strategies in Python using Numba | by Max Dmitrievsky | Medium, 檢索日期：5月 11, 2026， https://dmitrievsky.medium.com/a-quick-tester-of-trading-strategies-in-python-using-numba-0b62fda7d72d
- python - Pandas Efficient VWAP Calculation - Stack Overflow, 檢索日期：5月 11, 2026， https://stackoverflow.com/questions/29298789/pandas-efficient-vwap-calculation
- JadenJ09/ta-numba - GitHub, 檢索日期：5月 11, 2026， https://github.com/JadenJ09/ta-numba
- How to Backtest a VWAP Trading Strategy in Python - QuantVPS, 檢索日期：5月 11, 2026， https://www.quantvps.com/blog/backtest-vwap-trading-strategy-python
- How to calculate VWAP in Python with Databento and pandas, 檢索日期：5月 11, 2026， https://databento.com/blog/vwap-python
- Volume Weighted Average Price (VWAP): Calculation and Visualization Using Pandas, 檢索日期：5月 11, 2026， https://codesignal.com/learn/courses/technical-indicators-in-financial-analysis-with-pandas/lessons/volume-weighted-average-price-vwap-calculation-and-visualization-using-pandas
- Advanced Futures Trading Strategies for Active Traders - MetroTrade, 檢索日期：5月 11, 2026， https://www.metrotrade.com/advanced-futures-trading-strategies/
- Automated Futures Trading Strategies: How to Build, Backtest & Scale - QuantVPS, 檢索日期：5月 11, 2026， https://www.quantvps.com/blog/automated-futures-trading-strategies
- 5 ATR Stop-Loss Strategies for Risk Control - LuxAlgo, 檢索日期：5月 11, 2026， https://www.luxalgo.com/blog/5-atr-stop-loss-strategies-for-risk-control/
- Chandelier Exit | ChartSchool | StockCharts.com, 檢索日期：5月 11, 2026， https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/chandelier-exit
- Chandelier Exit Explained: Smarter Trade Exits Made Simple - Enlightened Stock Trading, 檢索日期：5月 11, 2026， https://enlightenedstocktrading.com/chandelier-exit/
- 三重濾網交易系統怎麼看？用3步驟快速提高交易勝率！ - winsmart.tw, 檢索日期：5月 11, 2026， https://winsmart.tw/online_teaching/%E4%B8%89%E9%87%8D%E6%BF%BE%E7%B6%B2/
