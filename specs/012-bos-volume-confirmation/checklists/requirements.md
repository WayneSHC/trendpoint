# Specification Quality Checklist: BOS 續勢進場的量能確認濾網

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-07-30
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs) — *見 Notes 1（本 repo 慣例的刻意偏離）*
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders — *見 Notes 1*
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain（0 個；不確定處以 Assumptions 記錄推定值）
- [x] Requirements are testable and unambiguous（FR-001~012 皆有對應 SC）
- [x] Success criteria are measurable（差異數為 0／逐值相等／逐筆一致／非 NaN 等可判定條件）
- [x] Success criteria are technology-agnostic (no implementation details) — *見 Notes 2*
- [x] All acceptance scenarios are defined（US1×3、US2×3、US3×2）
- [x] Edge cases are identified（7 項，含訊號層互斥副作用與 backtest/live 漂移）
- [x] Scope is clearly bounded（Assumptions 明列 5 項範圍外事項）
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification — *見 Notes 1、2*

## Requirements ↔ Success Criteria 對照（憲章原則 III）

| FR | 對應 SC | 自動化 |
|---|---|---|
| FR-001（量能條件、平均量僅用判定根之前） | SC-003、SC-006 | pytest |
| FR-002（預設關閉、基準不變） | SC-001、SC-009 | pytest |
| FR-003（參數獨立於 MSS 量能乘數） | SC-008 | pytest |
| FR-004（不改訊號層語意） | SC-002 | pytest |
| FR-005（不疊加於反轉進場分支） | SC-008 | pytest |
| FR-006（空方鏡像對稱） | SC-004 | pytest |
| FR-007（未成熟/缺值/零量視為不通過） | SC-005 | pytest |
| FR-008（納入消融清單） | SC-007 | pytest 或實跑 |
| FR-009（參數集中 + ticker_overrides） | SC-009（schema 驗證測試） | pytest |
| FR-010（回測與監控消費同一判定） | SC-011 | **[MANUAL]** |
| FR-011（look-ahead 防禦測試） | SC-006 | pytest |
| FR-012（扣成本後指標、勝率僅輔助） | SC-010 | **[MANUAL]** |

未被任何 FR 涵蓋的 SC：無。未被任何 SC 涵蓋的 FR：無。

## Notes

1. **「無實作細節」為刻意偏離**：本 repo 的既有規格（如 `specs/011-unadjusted-sizing-price`）
   一律以 `檔案:行號`、欄位名與模組名錨定現況，因為憲章原則 III 要求規格的驗收標準必須
   對應 pytest 測試、原則 V 要求參數集中於指定設定檔。完全技術中立的寫法會使 FR-004、
   FR-010 這類「哪一層不可以被改動」的約束無法表達。故保留錨點，但不指定函式簽名、
   參數名稱或資料結構——那些留給 `/speckit-plan`。
2. **SC 的技術指涉**：SC-009~011 指名 `pytest -q`、`run_ablation.py`、
   `monitor_signals.py --once`，係憲章原則 III 對 `[MANUAL]` 驗收要求「明確說明人工驗證
   步驟」的直接後果，非疏漏。
3. **本規格未經實證門檻把關**：`docs/reviews/2026-07-30-wma-strategy-review.md` 建議的
   前置實驗（消融基準、長均線週期掃描）需要真實市場資料，開立本規格的環境無法取得
   （無 `trendpoint.db`、網路政策阻擋行情來源）。已於 spec 的 Assumptions 如實標註。
   SC-010 即為補上該門檻的驗收條件。
4. 驗證迭代次數：1（首次撰寫即全項通過，無需修正迭代）。
