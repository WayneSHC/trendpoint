# Specification Quality Checklist: 期貨連續序列未調整參考價（sizing 與期交稅價格基準修正）

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-07-18
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

- Items marked incomplete require spec updates before `/speckit-clarify` or `/speckit-plan`
- Input 引文與背景段保留使用者原文中的程式/資料表名稱（fut_TXF_daily、
  trading_costs.py 等）作為佐證脈絡；FR/SC 本體以領域語言撰寫，不綁實作。
- 「歷史保證金率/稅率時變」明確劃出範圍外（見 Assumptions），避免 scope 蔓延。

### 2026-07-18 第二輪 review（對照程式碼）修正記錄

1. **FR-008 ↔ FR-009 原為矛盾**：兩條都掛在「欄位在不在」這個判斷點上，
   卻要求相反行為（硬失敗 vs 退化 fallback），實作時必有一條失效。已改為
   把區分點移到來源義務層——所有期貨來源皆須產出該欄位（無調整者填收盤價），
   使「欄位缺失」唯一代表舊資料，硬失敗因而無歧義。
2. **FR-008 作用域限縮**：現貨 sizing 不消費此欄位，若寫成全域資料契約檢查
   會誤擋現貨表。已明訂僅作用於期貨 sizing／稅路徑。
3. **FR-005 稅基修正**：程式碼顯示 sizing 取訊號根收盤、稅取滑價後成交價
   （兩個不同時點），故稅不能直接代入未調整收盤價。當時改為經該根調整位移量
   （調整後收盤 − 未調整參考價）換算，並新增 SC-006／SC-007 對應驗收。
   **此換算機制已於第三輪推翻，見下。**

### 2026-07-18 第三輪（plan 階段對照程式碼）修正記錄

4. **位移量換算違反憲章原則 I，已禁止**：位移量 = 該時點之後所有轉倉調整
   的總和，是**未來事件的函數**，且非截斷不變——`data_sources/rollover.py:19-20`
   自載「back-adjust 平移基準隨尾端而異」。以它換算稅基等於把看前偏誤引入
   摩擦成本。改為 FR-001 直接攜帶未調整 **OHLC 四欄**（稅取成交根
   `unadj_open` + 滑價，sizing 取訊號根 `unadj_close`），並新增 FR-011
   明文禁止回推、SC-008 以截斷不變性測試釘死。
5. **連帶修正**：SC-002 抽驗範圍由「原始收盤價」擴為「原始 OHLC 四欄」；
   Assumptions 第一條移除已失效的「經位移量換算為精確值」敘述（該敘述與
   FR-011 直接矛盾）。
6. **編號順序整理**：FR-011 移至 FR-010 之後、SC-008 移至 SC-007 之後。
