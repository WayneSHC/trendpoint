# Specification Quality Checklist: 進場閘門（回撤上限 + 結算日封鎖）

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-07-30
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs) — *見 Notes 1（本 repo 慣例的刻意偏離）*
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders — *見 Notes 1*
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain（0 個；不確定處以 Assumptions 記錄推定值與理由）
- [x] Requirements are testable and unambiguous（FR-001~015 皆有對應 SC）
- [x] Success criteria are measurable（差異數為 0／可指出確切根數／schema 拒絕／逐筆相同）
- [x] Success criteria are technology-agnostic (no implementation details) — *見 Notes 2*
- [x] All acceptance scenarios are defined（US1×4、US2×3、US3×4）
- [x] Edge cases are identified（8 項，含「閘門不得阻擋出場」與時序取值陷阱）
- [x] Scope is clearly bounded（FR-015 明文排除縮減部位；Assumptions 列 7 項範圍外事項）
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification — *見 Notes 1、2*

## Requirements ↔ Success Criteria 對照（憲章原則 III）

| FR | 對應 SC | 自動化 |
|---|---|---|
| FR-001（閘門阻止開新倉、無方向性） | SC-002、SC-010 | pytest |
| FR-002（不影響任何出場路徑） | **SC-003** | pytest |
| FR-003（回撤觸發與恢復） | SC-002 | pytest |
| FR-004（僅用判定根為止之權益） | SC-004 | pytest |
| FR-005（恢復門檻嚴格小於封鎖門檻） | SC-005 | pytest |
| FR-006（結算日＝第三個週三，假日後推） | SC-006、SC-007 | pytest |
| FR-007（僅期貨適用） | SC-008 | pytest |
| FR-008（兩道可獨立開關） | SC-009 | pytest |
| FR-009（預設關閉、逐筆位元不變） | SC-001、SC-013 | pytest |
| FR-010（封鎖原因可辨識） | SC-012 | pytest |
| FR-011（納入消融清單） | SC-011 | pytest 或實跑 |
| FR-012（參數集中 + ticker_overrides） | SC-005、SC-013 | pytest |
| FR-013（look-ahead 防禦測試） | SC-004 | pytest |
| FR-014（風險調整後指標裁決） | SC-014 | **[MANUAL]** |
| FR-015（不實作縮減部位） | — | *範圍約束，非可測行為（見 Notes 3）* |
| — | SC-015（門檻以 p95 回撤校準） | **[MANUAL]** |

未被任何 FR 涵蓋的 SC：SC-015（屬 FR-014 判讀紀律的延伸，標為 `[MANUAL]`）。
未被任何 SC 涵蓋的 FR：FR-015（範圍排除條款，見 Notes 3）。

## Notes

1. **「無實作細節」為刻意偏離**：沿用 `specs/011` / `specs/012` 的既有慣例——
   以 `檔案:行號` 錨定現況，因憲章原則 III 要求驗收標準對應 pytest、原則 V 要求
   參數集中於指定設定檔。完全技術中立的寫法無法表達 FR-004 這類
   「哪一根的權益可以用」的時序約束，而該約束正是本案最容易被誤實作的地方。
   函式簽名、參數名稱與資料結構仍留給 `/speckit-plan`。

2. **SC 的技術指涉**：SC-013~015 指名 `pytest -q`、`run_ingestion.py`、
   `run_ablation.py`、`monte_carlo`，係憲章原則 III 對 `[MANUAL]` 驗收要求
   「明確說明人工驗證步驟」的直接後果。

3. **FR-015 無對應 SC 是正確的**：它是**範圍排除條款**（「本案不做縮減部位」），
   不是可觀察行為。硬要為它寫測試會變成「斷言某功能不存在」，
   那種測試在後續 spec 014 實作縮減部位時必然要刪——屬於自找的維護債。
   範圍邊界由 code review 與 `/speckit-analyze` 把關，不由測試把關。

4. **本案的最高風險是 FR-002 的誤實作**：一道會擋住停損的「風控」比沒有風控更糟。
   已在 Edge Cases 明文列為「本案最危險的可能誤實作」，並以 SC-003 專門守門。
   plan 階段須確認閘門的接線點在**開新倉的判定分支內**，而非在迴圈更前面的
   共用位置（後者會同時攔到出場路徑）。

5. **與 spec 012 的關鍵差異**：012 只多一道進場濾網；本案**改變權益路徑本身**
   （少做幾筆 → 權益曲線不同 → 所有績效指標不同）。因此 SC-001 的「逐筆位元
   不變」比 012 更關鍵，且必須比對**權益曲線逐根值**而非僅摘要指標。

6. **裁決指標的陷阱已寫進 FR-014**：本案預期降低總報酬（曝險下降）、提高
   風險調整後指標。若以總報酬裁決，會把一個有效的風控功能誤判為有害而砍掉。
   這一點必須在跑 SC-014 之前就講定，不能事後解釋。

7. 驗證迭代次數：1（首次撰寫即全項通過，無需修正迭代）。
