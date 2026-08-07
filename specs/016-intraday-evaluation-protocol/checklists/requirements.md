# Specification Quality Checklist: 盤中時框評估協定

**Purpose**: 於進入規劃階段前驗證規格完整性與品質
**Created**: 2026-08-06
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] 無實作細節（語言、框架、API）——僅在 Assumptions 標示既有依賴，未規定實作方式
- [x] 聚焦使用者價值與業務需求——四個 user story 各自對應一個可獨立交付的評估能力
- [x] 為非技術關係人所寫
- [x] 所有必填章節皆已完成

## Requirement Completeness

- [x] 無 `[NEEDS CLARIFICATION]` 標記殘留（Q1 累積歷史存放方式已於 D1 裁決）
- [x] 需求可測試且無歧義
- [x] 成功標準可量測
- [x] 成功標準與技術無關（不含實作細節）
- [x] 所有驗收情境皆已定義
- [x] Edge case 已識別（6 項，含資料飄動、暖機吃滿、鏈結斷裂）
- [x] 範圍邊界清楚（Out of Scope 列 6 項，含期貨盤中與參數時框實作）
- [x] 依賴與假設已識別

## Feature Readiness

- [x] 所有功能需求皆有對應的驗收標準
- [x] User story 具優先序且可獨立測試
- [x] 成功標準可量測（SC-001 ~ SC-013）
- [x] 無實作細節洩漏至規格

## 憲章對照（`.specify/memory/constitution.md`）

- [x] **原則 I 看前偏誤**：本規格不新增訊號計算路徑；沿用既有
      `build_indicator_frame` 之 `.shift(1)` 語意。FR-019 保證生產路徑零變更。
- [x] **原則 II 摩擦成本**：評估沿用 `BacktestEngine` 的成本模型，
      費率單一來源仍為 `config/config.yaml` 的 `trading_cost`。
- [x] **原則 III 禁止有效性宣稱**：FR-006 與 SC-012 明文焊死；
      FR-018 進一步禁止在無量測支持時輸出既定處方。
- [x] **原則 V 參數集中**：FR-021 禁止本規格新增硬編碼；
      既有 `structure_period=10` 標示為既有缺陷且明列於 Out of Scope。
- [x] **測試門檻**：SC-013 要求 `pytest` 全綠且每條驗收標準有對應測試。

## Notes

**已裁決**：D1（累積歷史存於 Actions artifact 滾動累積）。其兩項代價——
90 天保留期上限造成的斷鏈風險、公開 repo artifact 可被任意下載——
已於 spec 明文記錄為**接受的殘留風險**，非已解決問題。
規劃階段須將 FR-022/023/024 視為該裁決的直接後果一併設計。

**規劃階段須決定的具體閾值**（非阻斷性，已於 Assumptions 給出預設）：
納入準則的各維度門檻、樣本外解除門檻（預設 3 組窗口 × 每標的 30 筆）、
排程頻率（預設每週）、資料飄動取捨規則（預設先到者為準）。
