# Specification Quality Checklist: 排程與持久化 as-built ＋ 帳遷移至 repo 內純文字檔

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-07-31（同日修訂：託管資料庫方案經 Phase 0 推翻，見 ADR 0004）
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

### 三處刻意的偏離

1. **「現行行為（As-Built）」一節刻意含實作細節**（工作流名稱、cache key 構成、
   `trendpoint.db`、`sent_alerts`）。該節的目的就是記錄變更前的既有行為作為驗收
   對照基準，抽掉這些細節就失去作用。

2. **FR 中出現 JSONL、UTC 月份、rebase、`.gitignore` 等具體項**。這與初版
   （全篇技術中立、供應商留待 plan 階段）不同，是刻意的改變：ADR 0004 之後，
   「帳是 repo 內受版本控制的純文字檔」**本身就是需求**而非實作選擇——
   它承載了「不可靜默回退」「可逐次追溯」「無外部依賴」三項使用者價值，
   而這些性質正是由該形式提供的。把它抽象成「某種儲存」會使 FR 無法驗證。
   仍然刻意不寫的是：具體的 JSON 欄位名、模組名、函式簽章（那些在
   [data-model.md](../data-model.md) 與 [contracts/](../contracts/append-only-store-contract.md)）。

3. **「非技術性讀者」在本 spec 指系統維護者**。這是基礎設施類 spec，
   其使用者本質上是維護者而非終端使用者；US 皆以維護者的實際痛點
   （重複推播、紀錄靜默遺失、工作流假綠燈）敘述，而非以內部機制敘述。

### 相對初版的結構變化

- **US 由 4 併為 3**：原 US3（獨立快照）併入 US1——帳與快照收斂為同一個物件。
- **SC 由 6 增為 7**：新增 SC-007（併發不覆蓋），因純文字檔＋git 引入了
  初版沒有的衝突面。
- **外部前置條件消失**：初版需 repo owner 註冊託管服務帳號並設定 Secrets，
  阻塞 US1／US3／US4 的真實環境驗收。現版**無任何外部依賴**，全部可即刻驗收。
