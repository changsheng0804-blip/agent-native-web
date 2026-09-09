# Phase 1：Astra × Agent-Native Canvas A/B/C Harness 验证

## 研究问题

本实验验证一个单一问题：

> 对已经具备强视觉与 Computer Use 能力的 GPT-6 Astra，Agent-native 的结构化世界状态、动作与验证接口，是否仍能提供可测量的可靠性或效率增益？

本目录只定义实验协议、权限边界、结果格式和分析规则；不修改生产 MCP 行为。

Phase 0 已由 `tests/fixtures/agent_native_canvas.html` + `mcp/test_enhancements.py` 验证：Canvas 图形没有结构化 DOM 节点，但环境自身的 scene model 可以提供 `observe / act / verify`，并完成“观察 → 行动 → 测量 → 修正 → 再验证”的闭环。

## 固定任务

所有实验臂执行同一任务：

> 把黄色五角星精确放到蓝色圆的中心。

任务事实、成功条件、预算见 `task.json`。

## 三个实验臂

### A — Astra Native

模型可见/可用：

- 视觉截图
- Computer Use
- 鼠标/键盘等原生界面动作

模型不可见/不可用：

- `agentCanvas.observe()`
- `agentCanvas.act()`
- `agentCanvas.verify()`
- Agent-Native Web/Canvas 的结构化状态

A 组是纯视觉 + Computer Use 基线。

### B — Structured

模型可见/可用：

- `observe`
- `act`
- `verify`

模型不可见/不可用：

- 任务截图作为推理输入
- Computer Use 坐标点击/拖动

B 组测试：Agent-native 环境本身是否足够形成精确闭环。

### C — Hybrid

模型同时可用：

- 视觉截图
- Computer Use
- `observe`
- `act`
- `verify`

不得提示模型“优先视觉”或“优先结构化接口”。让模型自行选择信息源和行动路径。

完整权限矩阵见 `arms.json`。

## 公平性规则

1. A/B/C 必须使用同一个任务说明、同一个初始 scene、同一个成功条件。
2. 每次 run 前必须 reset 到完全相同的初始状态。
3. 第一轮 Pilot 每臂 5 次，共 15 runs。
4. 不允许根据中途结果修改 prompt、工具权限、成功阈值或预算。
5. 如果某个 run 因外部基础设施故障无效，必须标记 `invalid_run=true` 并说明原因；不得直接当作失败或静默重跑。
6. 模型自己声称“完成”不等于实验成功。
7. 真正 success 只能由 Harness 在模型结束后读取隐藏 ground truth 判定。
8. A/B 组中被模型禁止访问的信息，Harness 仍可在 run 结束后用于裁判和日志，但不得注入模型上下文。

## Ground Truth（真实裁判）

统一使用 Canvas 环境的最终真实状态判定：

- `center_error_px == 0`
- `star_inside_circle == true`

模型结束时记录：

- `claimed_success`
- `success`
- `false_positive = claimed_success && !success`

这是防止“模型认为成功，但环境实际未成功”的关键指标。

## 第一轮核心指标

必须记录：

- success rate
- false-positive rate
- elapsed time
- total action count
- correction count
- final center error

辅助记录：

- screenshots
- Computer Use actions
- structured observations
- structured actions
- verify calls
- tokens（若执行环境可提供）
- raw trace

统一结果格式见 `result.schema.json`。

## 预注册判定原则

实验前固定以下解释规则，避免看到结果后再调整口径：

1. **C 与 A 成功率相同，但 C 明显更快/动作更少**：Hybrid 有效率价值。
2. **C 比 A 假成功更少**：结构化 verify/outcome 有可靠性价值。
3. **A 与 C 都 100% 成功，但 A 更快且动作更少**：当前简单任务中结构化反馈是负收益；应增加更复杂场景，而不是强行解释为有效。
4. **B ≈ C 且优于 A**：主要价值来自结构化环境，而不是视觉融合。
5. **C 同时优于 A 和 B**：支持“视觉理解 + 结构化测量/验证”存在互补价值。
6. Pilot 只有 5 runs/臂，只用于发现明显信号，不用于做强统计结论。

## 执行角色

### 非 Astra Harness（例如 DeepSeek Harness）

可以：

- 实现 runner
- 检查 reset 是否可靠
- 检查 A/B/C 工具隔离
- 生成符合 schema 的 JSONL
- 做 dry-run / smoke test
- 修复实验执行器本身的问题

不能：

- 用自己的模型结果替代 Astra 的 Phase 1 结论

### Astra 执行环境

最终 Phase 1 必须在真正能运行 GPT-6 Astra + Computer Use 的环境执行同一协议。

如果使用 OpenAI API，模型为 `gpt-6-astra`，工具调用应使用 Responses API。若使用 Codex/Work，则必须先确认当前账户/客户端实际已经获得 Astra 与 Computer Use 能力；仅进入 Codex 并不自动等于完成 Astra 验证。

## 输出目录建议

Harness 不应提交包含敏感页面内容的原始日志到主仓库。建议本地输出：

```text
artifacts/astra_canvas_phase1/<timestamp>/
  runs.jsonl
  summary.json
  raw/
    A-001.json
    ...
```

`runs.jsonl` 必须符合 `result.schema.json`。

## 下一阶段

只有 Phase 1 拿到真实 Astra 数据后，才决定是否：

- 扩展更多 Canvas 任务；
- 引入视觉/结构分歧指标；
- 抽象通用 Environment Runtime；
- 修改 `world_*` 协议；
- 进入桌面应用、3D/CAD 等环境。
