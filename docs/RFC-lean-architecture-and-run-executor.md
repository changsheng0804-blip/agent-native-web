# RFC: Agent-Native Web Lean Architecture and Optimization

## Summary
This RFC proposes an architectural lean-refactor for `agent-native-web` (formerly `agent-world-mcp`) inspired by modern Agent browser patterns (like BrowserOS Neo) while strictly preserving agent-world's core differentiator: deterministic, physical-evidence closed-loop feedback without guessing.

---

## 1. Motivation & Problem Analysis
- **Current Core Strengths**:
  - CAD-like Spatial Blueprint (`world_map`, `world_layers`, `world_entities`)
  - Strict 5-state Page Outcome (`progressed`, `unchanged`, `challenged`, `uncertain`, `errored`)
  - Dual Mutation (DOM + Visual Diff) preventing false-positive successes.
- **Identified Bloat**:
  1. **Too many granular MCP tools (22 currently)**: High token footprint in LLM prompt, excessive Round-Trips for multi-step tasks.
  2. **Heavy Payload Returns**: Full `status` and element lists returned on every single action (`_inject_status`).
  3. **Missing Occlusion Attribution**: `unchanged` verdict doesn't clearly distinguish coordinate miss vs transparent overlay blocking.

---

## 2. Proposed Architecture Improvements

### A. Aggregate Execution Engine (`world_run`)
- Introduce a single high-efficiency `world_run` MCP tool allowing agents to run an async JavaScript / DSL pipeline in a single MCP round-trip:
  ```javascript
  const w = await world.open('https://example.com/form');
  await w.batchFill([
    { id: 'el_6', text: 'Tan' },
    { id: 'el_12', text: 'tan@example.com' }
  ]);
  const outcome = await w.click('el_45');
  return outcome;
  ```
- **Benefit**: Compresses 4-6 round-trips into 1; reduces MCP tool definitions from 22 down to 4-5 core tools.
- **修正(2026-09-02 评审)**:
  - 阶段 A 已完成:所有动作(click/fill/press/batch_fill/click_at/navigate)已统一走 `_outcome_card` 出口,五态平铺主标签已落地。
  - `world_run` 不应成为第 23 个平行工具,而应作为**阶段 B 收口环中 `world_act` 的聚合形态**:内部复用 `_outcome_card` 同一出口,契约不 fork。弱模型只学 `open → guide → find → act → outcome` 六词。
  - **安全闸门**:DSL 白名单化,只允许 open/batchFill/click/press/outcome 序列,不做自由 JS(`world_eval` 在 CDP 会话下已禁用,正是同一安全边界)。

### B. Lightweight Diff-First Payloads
- Keep the rock-solid `page_outcome` state machine.
- Instead of returning massive JSON on every click, return compact topology diffs:
  ```text
  [Outcome: progressed]
  - gone: el_17 (button.submit)
  + added: el_48 (div.success-message: 'Submitted Successfully')
  url: https://example.com/success
  ```
- Output deep diagnostic trees only when `page_outcome == 'unchanged'` or `uncertain`.
- **修正(2026-09-02 评审)**:
  - `effect.observed`(add/remove + el_N)与 `overlays` 已随统一卡返回,diff 输出已有雏形。
  - 真正的增量是**默认轻载荷 + `verbose: true` 可选深诊断**,而非直接砍掉 `status`(向后兼容):`_inject_status` 全量注入改为按需。

### C. Collision & Occlusion Diagnostics (`elementFromPoint`)
- Add a lightweight `document.elementFromPoint(x, y)` check in runtime before simulated clicks.
- If top element does not match target, explain the physical blockage immediately:
  `Element el_20 is covered by <div class='modal-backdrop'> at (35, 223). Dismiss overlay first.`
- **修正(2026-09-02 评审)**:
  - 已有基础:`_t_world_click` 已实现 `hit_info`/`obscured_note`(elementFromPoint 检测)。
  - 增量:把提示结构化(`covered_by` / `at` / `action`),并覆盖 fill/press/click_at 全部动作出口。半天工作量,可独立成小 PR。

---

## 3. Plan & Rollout (2026-09-02 评审后修订)

- Phase 0(已完成):统一后果卡 `_outcome_card` 全动作五态出口 + challenged/错误信号检测 + world_epoch;offline 16/16 全绿。
- Phase 1:阶段 B 收口——`world_find` / `world_act`(含聚合序列)/ `world_outcome` 门面,`world_run` 作为 `world_act` 的聚合形态落地,复用 `_outcome_card`。
- Phase 2:Diff-First 载荷——`_inject_status` 默认轻量 + `verbose` 选项;unchanged/uncertain 时返回深诊断树。
- Phase 3:遮挡归因结构化——增强现有 `obscured_note` 为 `covered_by/at/action` 固定格式,覆盖全部动作。
- 约束:不新增平行工具语义;DSL 白名单;CDP 会话安全闸门不变。
