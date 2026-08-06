# Specification Quality Checklist: 推播訊號的事後表現追蹤（A 段）

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-05
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

### 驗證過程中發現並修正的問題

**第 1 輪發現：7 條 FR 沒有對應的驗收標準。** 初稿的 SC-001~018 未涵蓋
FR-004（欄位完整性）、FR-006（時框分群）、FR-012（不引入新資料源／排程）、
FR-016（分頁篩選）、FR-020（參數集中）、FR-022（參數化查詢）、FR-023（不含憑證）。
這直接違反憲章原則 III（每條驗收標準須對應測試、規格與程式不得沉默漂移）。

**處理**：改寫 Success Criteria 為 SC-001~023 並分節（既有行為保護／記錄正確性／
持久化／回填正確性／呈現與治理／人工驗收），新增「需求 ↔ 驗收對照」表，
使 23 條 FR 全數有明確落點。第 2 輪複驗通過。

### 保留的判斷（非缺失，記錄理由）

1. **關於「No implementation details」**：規格含 `檔案:行號` 形式的既有程式碼引用
   （如 `monitor_signals.py:194-196`），SC-019 亦點名既有模組。這些是**界定變更邊界
   與既有事實的佐證**，不是實作處方——規格全程未指定資料表名稱、檔案格式、
   函式簽章或任何新元件的構造方式。此作法與 `specs/014` 一致，屬 repo 慣例。

2. **關於「Written for non-technical stakeholders」**：本規格假設讀者具備本專案的
   領域詞彙（K 線、告警、回測、時框）。此為單人技術專案，讀者即 repo 擁有者，
   與 specs/001~014 的既有寫法一致。

3. **關於 SC-021（`pytest -q` 全綠）看似技術特定**：此為憲章
   「Development Workflow & Quality Gates」第 2 條明訂的硬性關卡，
   且 spec 013（SC-013）已有同樣寫法，屬 repo 既定慣例而非本案引入的實作細節。

### 未使用 [NEEDS CLARIFICATION] 的理由

本規格 0 個澄清標記。兩個原本可能需要澄清的決策點，使用者已在 specify 前明確選定：

- **儲存方案** → 「有新訊號才 commit JSONL 進 repo」
- **範圍** → 「只做 A 段日線視窗」

第三個潛在爭點（將含價格快照的紀錄納入版本庫，與 `.gitignore` 對 `data/` 所載明的
「Yahoo Finance 資料再散布疑慮」同源）**不以澄清標記處理，而是記入 Assumptions A-1**：
使用者的選擇已明確，規格的責任是把該因素與替代路徑寫清楚，而非重問一次。
若使用者讀後認定風險不可接受，A-1 已載明替代方案僅影響 FR-008 的實作方式。

### 進入下一階段前的提醒

- Assumptions **A-6（樣本頻率未知）是本案最大的不確定性**，已由 SC-022 設為
  第一個人工檢驗點。`/speckit-plan` 階段應確保該檢驗**先於**儀表板呈現層的投入。
- Assumptions **A-9（排程環境寫入權限）** 是部署前提，plan 階段需確認可得，
  否則 A-1 的替代方案生效。
