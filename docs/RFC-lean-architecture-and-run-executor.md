# RFC: Agent-Native Web Lean Architecture and Optimization

## Summary
This RFC proposes an architectural lean-refactor for `agent-world-mcp` inspired by modern Agent browser patterns (like BrowserOS Neo) while strictly preserving agent-world's core differentiator: deterministic, physical-evidence closed-loop feedback without guessing.

---

## 1. Motivation & Problem Analysis
- **Current Core Strengths**:
  - CAD-like Spatial Blueprint (`world_map`, `world_layers`, `world_entities`)
  - Strict 5-state Page Outcome (`progressed`, `unchanged`, `challenged`, `uncertain`, `errored`)
  - Dual Mutation (DOM + Visual Diff) preventing false-positive successes.
- **Identified Bloat**:
  1. **Too many granular MCP tools (15+)**: High token footprint in LLM prompt, excessive Round-Trips for multi-step tasks.
  2. **Heavy Payload Returns**: Full `status` and element lists returned on every single action.
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
- **Benefit**: Compresses 4-6 round-trips into 1; reduces MCP tool definitions from 15 down to 4-5 core tools.

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

### C. Collision & Occlusion Diagnostics (`elementFromPoint`)
- Add a lightweight `document.elementFromPoint(x, y)` check in runtime before simulated clicks.
- If top element does not match target, explain the physical blockage immediately:
  `Element el_20 is covered by <div class='modal-backdrop'> at (35, 223). Dismiss overlay first.`

---

## 3. Plan & Rollout
- Phase 1: Implement `world_run` tool in `mcp/server.py`.
- Phase 2: Add `elementFromPoint` occlusion check in `extension/runtime/`.
- Phase 3: Optimize action return payload to be Diff-First.
