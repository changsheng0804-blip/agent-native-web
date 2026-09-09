# HARNESS HANDOFF — Astra Canvas Phase 1

## 目标

严格执行本目录定义的 A/B/C Pilot，不重新设计实验。

## 输入

必须读取：

- `README.md`
- `task.json`
- `arms.json`
- `result.schema.json`
- `tests/fixtures/agent_native_canvas.html`

## 执行职责

1. 为每个 arm 建立独立、全新模型上下文。
2. 每次 run 前 reset Canvas 到相同初始 scene。
3. 严格按 `arms.json` 隔离模型可见信息和可调用动作。
4. A/B/C 共用同一任务文本、预算和成功裁判。
5. 每臂先执行 5 次有效 run。
6. 每个 run 结束后由 Harness 读取隐藏 ground truth；不得让模型自行决定实验 success。
7. 输出一行一个 JSON 到 `runs.jsonl`，字段符合 `result.schema.json`。
8. 保存 raw trace，至少包含：模型输入摘要、工具/Computer Use 调用序列、时间戳、最终模型文本、最终 ground truth。
9. 基础设施失败必须显式标记 `invalid_run=true`，不得静默重跑或计为模型失败。
10. 完成后运行 `analyze_results.py runs.jsonl` 生成汇总。

## 两阶段执行建议

### Stage H0 — Harness Dry Run

可使用 DeepSeek、Kimi、Sol 或其他可用模型，目的只是验证实验执行器：

- reset 正确；
- A 组看不到结构接口；
- B 组看不到截图/Computer Use；
- C 组两者都能用；
- ground-truth judge 与模型上下文隔离；
- JSONL 输出符合 schema；
- 计时、动作计数、invalid_run 处理正确。

H0 的模型表现数据**不得**作为 GPT-6 Astra Phase 1 结论。

### Stage H1 — Astra Real Run

使用真正的 GPT-6 Astra 执行同一协议，不改变任何已经冻结的任务/权限/判定规则。

若通过 OpenAI API：

- 模型：`gpt-6-astra`
- 工具调用：Responses API
- Computer Use：使用当前官方支持的 Computer Use 能力

若通过 Codex/Work：

- 先确认当前账户、客户端和工作区已经实际获得 Astra + Computer Use；
- 记录具体模型标识和 Harness/客户端版本；
- 不得因为 UI 中显示“Astra”就省略实际能力检查。

## 最小能力自检

在正式 15-run Pilot 前，执行器必须通过以下 smoke checks：

### A arm

- 模型可获得截图；
- 模型可以产生 Computer Use 动作；
- 任何尝试调用 `observe/act/verify` 都必须被 Harness 拒绝或根本不暴露。

### B arm

- 模型可调用 `observe/act/verify`；
- 模型不能获得任务截图；
- 模型不能产生 Computer Use 坐标动作。

### C arm

- 两类能力均可用；
- system/task prompt 不得包含“结构更准确”“优先 verify”“优先视觉”等倾向性建议。

## Ground Truth 隔离

所有 arm 在模型结束后统一执行隐藏裁判：

```text
center_error_px == 0
AND
star_inside_circle == true
```

然后计算：

```text
false_positive = claimed_success && !success
```

A 组即使禁止模型访问 `agentCanvas.verify()`，Harness 裁判仍可在模型结束后调用它。该结果只进入日志，不回填模型。

## 输出建议

```text
artifacts/astra_canvas_phase1/<timestamp>/
  runs.jsonl
  summary.json
  raw/
    A-001.json
    A-002.json
    ...
```

不要把 API key、cookie、账号信息、完整敏感截图写入仓库。

## 禁止事项

- 不因某一组连续失败而临时加强其 prompt；
- 不为 C 组额外解释如何利用结构接口；
- 不把模型自述成功直接记为 success；
- 不静默删除失败 run；
- 不在得到 Astra 数据前修改生产 MCP/协议来“配合结果”；
- 不用 H0 的 DeepSeek/Kimi/Sol 数据替代 H1 Astra 数据。

## 交付给 Chat/Sol 的最小材料

完成 H1 后返回：

1. `runs.jsonl`
2. `summary.json` 或 `analyze_results.py` 输出
3. 每个失败/假成功 run 的 raw trace
4. 使用的模型、客户端/Harness 版本、执行日期
5. 任何 invalid run 及原因

随后再做 Phase 1 结果解释和下一阶段设计。
