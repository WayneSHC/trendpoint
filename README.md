# TrendPoint

> 以「市場結構分析」融合「動態波動率」的台股 / 台指期趨勢交易輔助系統。
> 將價格波動轉化為直觀的多空階梯支撐壓力線與動態交易區間，用客觀訊號協助克服情緒化決策。

TrendPoint 把多空階梯系統（Ladder System）、ATR 波動率錨定、台指期三關價全域濾網與市場結構動力學（MSS / BOS）整合成一套可回測、可優化、可即時監控的策略框架，並以 Streamlit 打造機構級交易工作站儀表板。

現行基準規格見 [`specs/001-ladder-core/spec.md`](specs/001-ladder-core/spec.md)；策略理論見 [`three_bands_theory.md`](three_bands_theory.md)；原始產品規格（歷史文件）見 [`TrendPoint_OpenSpec.md`](TrendPoint_OpenSpec.md)。

## 功能總覽

- **趨勢預測儀表板**：當前多空偏見（看多 / 看空 / 觀望）、三關價、Ladder 階梯價、風險調整後 KPI（CAGR / Sharpe / Sortino / Calmar）。
- **歷史回測**：向量化 + Numba 加速，內建滑點與手續費摩擦成本，並防禦看前偏誤（look-ahead bias）。
- **投資組合回測**：跨多標的組合層級回測。
- **參數尋優與 Walk-Forward 驗證**：樣本內尋優 / 樣本外驗證，避免過度擬合。
- **消融測試（Ablation）**：量化各進場濾網的邊際貢獻。
- **即時訊號監控與推播**：透過 LINE Messaging API 與 Telegram 推送 BOS / MSS / 三關價突破訊號，以及月／季／半年／年線的觸價通知。

## 環境需求

- Python 3.10+（CI 於 3.10 與 3.12 驗證）
- 相依套件見 [`requirements.txt`](requirements.txt)

## 安裝

```bash
git clone https://github.com/WayneSHC/trendpoint.git
cd trendpoint
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 設定

策略與資料設定集中於 [`config/config.yaml`](config/config.yaml)：監控標的、每標的參數覆寫（`atr_period`、`ladder_k`、`chandelier_mult` 等）、交易成本（手續費 / 證交稅 / 滑點）與初始資金。

預設標的：`2330.TW`、`0050.TW`、`00878.TW`、`00919.TW`、`00631L.TW`。

## 快速開始

```bash
# 1. 下載並持久化 K 線資料至 SQLite（trendpoint.db）
python run_ingestion.py

# 2. 啟動交易工作站儀表板
streamlit run app.py

# 3. 執行歷史回測
python run_backtest.py
```

其他工作流程：

| 指令 | 用途 |
| :--- | :--- |
| `python run_portfolio_backtest.py` | 投資組合（多標的）層級回測 |
| `python run_optimization.py` | 策略參數自動尋優 |
| `python run_walk_forward.py` | Walk-Forward 樣本內 / 樣本外驗證 |
| `python run_ablation.py` | 進場濾網消融測試 |
| `python monitor_signals.py --once` | 執行單次即時訊號檢測與推播 |
| `python monitor_signals.py --test-alert` | 發送一筆測試訊息驗證推播管道 |

## 即時通知設定

訊號監控支援 LINE 與 Telegram；未設定任何憑證時自動降級為 Mock 模式（終端機輸出 + 寫入 `alerts.log`）。

於專案根目錄建立 `.env`（已被 `.gitignore` 排除）：

```ini
# LINE Messaging API（舊版 LINE Notify 已於 2025-03-31 停止服務）
LINE_CHANNEL_ACCESS_TOKEN=你的頻道存取權杖
LINE_TO=推播目標的 userId 或 groupId

# Telegram Bot
TELEGRAM_TOKEN=你的 Bot Token
TELEGRAM_CHAT_ID=你的 chat id
```

GitHub Actions（[`.github/workflows/alert_scheduler.yml`](.github/workflows/alert_scheduler.yml)）每 30 分鐘自動執行一次監控，憑證以同名 Repository Secrets 提供。

### 均線觸價通知（月／季／半年／年線）

股價**向下穿越**月線／季線／半年線／年線時推播。於 `config/config.yaml` 設定，
**總開關預設關閉**：

```yaml
alerts:
  ma_alerts_enabled: false   # 改為 true 啟用
  monthly:     { enabled: true, period: 20 }
  quarterly:   { enabled: true, period: 60 }
  half_yearly: { enabled: true, period: 120 }
  yearly:      { enabled: true, period: 240 }
```

幾點設計說明：

- **觸發語意是「穿越」而非「低於」**：價格持續低於均線的期間不會重複通知
  （否則每根 K 線都會發一次）。同一標的同一條線**每交易日至多一則**。
- **想知道「現在的狀態」請看儀表板**：`app.py` 的「均線現況」表列出目前相對
  四條線的位置與乖離。這是穿越語意的補集——推播回答「剛剛發生什麼」，
  儀表板回答「現在是什麼狀態」。
- **均線由日線計算**（年線需 240 根日線，故須先執行 `run_ingestion.py` 累積歷史）；
  日線根數不足的線**不會通知也不會顯示數值**，而是標示「資料不足」——
  以 30 根日線算出的「年線」是誤導。
- 期貨標的不適用（連續序列經 back-adjust，價位水準與當年真實市價脫節）。

## 測試

```bash
pytest -q
```

CI 於每次 push 至 `main` 與每個 PR 自動執行測試套件（見 [`.github/workflows/tests.yml`](.github/workflows/tests.yml)），並額外驗證無 Numba 環境下的降級回退一致性。

## 專案結構

```
app.py                    Streamlit 交易工作站儀表板
ladder_system.py          多空階梯系統核心演算法
backtester.py             單標的回測引擎
portfolio_backtester.py   投資組合回測引擎
optimizer.py              參數尋優
walk_forward.py           Walk-Forward 驗證
monte_carlo.py            蒙地卡羅交易重抽
performance.py            績效與風險指標
risk_gates.py             進場閘門（回撤上限 / 結算日封鎖，預設關閉）
data_ingestion.py         K 線資料下載與清洗
monitor_signals.py        即時訊號監控
ma_lines.py               均線觸價通知的純函式元件
alerts.py                 LINE / Telegram 推播管理
config/                   設定（config.yaml）
specs/                    功能規格（Spec Kit）
tests/                    pytest 測試套件
```

## 規格驅動開發（Spec-Driven Development）

本專案採用 [GitHub Spec Kit](https://github.com/github/spec-kit) 工作流：

- **憲法**：[`.specify/memory/constitution.md`](.specify/memory/constitution.md) —
  不可協商的工程原則（看前偏誤防禦、真實摩擦成本、驗收標準必須映射至測試等）。
- **基準規格**：[`specs/001-ladder-core/spec.md`](specs/001-ladder-core/spec.md) —
  現行系統的 as-built 規格。
- **功能規格**：`specs/002` 起的各案；狀態以各 `spec.md` 的 Status 欄為準。
  已併入 main 的包含 FVG 確認（002）、台指期做空（003）、MSS 反轉進場（007）、
  資料層與期貨成本（008/009）、TAIFEX 真實資料源（010）、未調整參考價（011）、
  均線觸價通知（014）、進場閘門（013，**兩道閘門預設關閉**——是否改為預設啟用
  需真實資料實測後決定）；`012`（BOS 量能確認）規格與計畫齊備、尚未實作。

新功能開發流程：`/speckit-specify` →（必要時 `/speckit-clarify`）→ `/speckit-plan` →
`/speckit-tasks` → `/speckit-implement` → `/speckit-analyze`。

## 授權（License）

本專案以 [Mozilla Public License 2.0](LICENSE)（MPL-2.0）授權。

- 你可以自由使用、修改、散布本專案，包括整合進商業或閉源產品。
- 但**修改過的 MPL 授權檔案**在散布時必須以 MPL-2.0 公開原始碼（檔案層級 copyleft）。
- 注意：`data/` 內的市場資料快取來自 Yahoo Finance，其再散布權利不在本授權涵蓋範圍內。

## 免責聲明

本專案為交易研究與決策輔助工具，所有訊號與回測結果僅供參考，不構成投資建議。實際交易風險自負。
