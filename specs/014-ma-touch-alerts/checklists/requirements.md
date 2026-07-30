# Specification Quality Checklist: 均線觸價通知（月／季／半年／年線）

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-07-30
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs) — *見 Notes 1*
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders — *見 Notes 1*
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain（0 個——與原始描述的唯一差異已於審核結論
      與 Assumptions 明示，並附替代方案，見 Notes 2）
- [x] Requirements are testable and unambiguous（FR-001~012 皆有對應 SC）
- [x] Success criteria are measurable（僅發出一則／至多一則／誤差為 0／不中斷）
- [x] Success criteria are technology-agnostic (no implementation details) — *見 Notes 1*
- [x] All acceptance scenarios are defined（US1×4、US2×2、US3×2）
- [x] Edge cases are identified（8 項，含跳空跌破、資料時效、期貨排除理由）
- [x] Scope is clearly bounded（FR-010 排除期貨；Assumptions 明列「不做向上突破」
      「不做狀態播報」「不進回測路徑」）
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Requirements ↔ Success Criteria 對照（憲章原則 III）

| FR | 對應 SC | 自動化 |
|---|---|---|
| FR-001（四條均線、週期可調） | SC-006、SC-007 | pytest |
| FR-002（由日線表計算、僅已收盤） | SC-006 | pytest |
| FR-003（比較價取即時路徑最新已收盤棒） | SC-002 | pytest |
| FR-004（向下穿越語意，非狀態） | **SC-003** | pytest |
| FR-005（每標的每線每日至多一則） | SC-004 | pytest |
| FR-006（資料不足明確不發、不用 min_periods=1） | **SC-005** | pytest |
| FR-007（各線獨立開關 + 總開關預設關閉） | SC-007 | pytest |
| FR-008（既有六種告警不受影響） | SC-001 | pytest |
| FR-009（訊息欄位完整） | SC-008 | pytest |
| FR-010（限現貨、排除期貨） | — | *範圍約束，見 Notes 3* |
| FR-011（參數集中 + schema） | SC-007、SC-010 | pytest |
| FR-012（日線表缺失時跳過不中斷） | SC-009 | pytest |
| — | SC-011（實跑 monitor 觀察輸出） | **[MANUAL]** |

未被任何 FR 涵蓋的 SC：SC-011（人工觀察，屬憲章原則 III 的 `[MANUAL]` 條款）。
未被任何 SC 涵蓋的 FR：FR-010（範圍排除條款，見 Notes 3）。

## Notes

1. **技術指涉的分寸**：本規格引用 `檔案:行號` 以錨定現況（去重鍵、5 分線路徑、
   `min_periods=1` 的既有反例），但不指定函式簽名、參數名稱或資料結構——那些留給
   `/speckit-plan`。此與 `specs/011`～`013` 的既有慣例一致。

2. **與原始需求的唯一字面差異，已明示而非默默改掉**：使用者說「達到或低於」，
   規格採「向下穿越」。理由是可驗證的——去重鍵含 `bar_time`
   （`monitor_signals.py:44-50`），狀態式判定會在價格持續低於期間**每根發一次**。
   Assumptions 已寫明：若使用者真正要的是「每天提醒我它還在年線下」，
   那是狀態播報，屬另一個功能。**這是需要使用者確認的一點，但不阻擋 plan 階段**
   ——兩種語意共用同一套均線計算與資料路徑，差別只在觸發判定，
   即使日後改變主意也不需重做設計。

3. **FR-010 無對應 SC 是正確的**：它是範圍排除條款（本案不處理期貨），不是可觀察行為。
   為它寫測試會變成「斷言某功能不存在」，日後若擴充期貨必然要刪。
   範圍邊界由 code review 與 `/speckit-analyze` 把關。
   （同 `specs/012` 的 FR-015、`specs/013` 的 FR-015 之處理原則。）

4. **本案的最高風險是 FR-008 的誤實作**：既有六種告警走 5 分線即時路徑，
   而本案要讀日線表。若實作時「順手」把整個監控改成日線路徑，
   所有既有推播的行為都會改變——那正是 `CLAUDE.md` 監控段記錄為「刻意設計」的部分。
   plan 階段須確認兩條資料路徑**並存**而非取代。

5. **與 012／013 的性質差異**：本案是**通知層**功能，不進入訊號或回測路徑，
   因此不需要「預設關閉 + 前後回測對照 + 消融」那一整套。它需要的是
   SC-001 的「既有告警不變」與 SC-011 的實跑觀察。規格篇幅相應較短，
   這是刻意的比例配置，非疏漏。

6. 驗證迭代次數：1（首次撰寫即全項通過）。
