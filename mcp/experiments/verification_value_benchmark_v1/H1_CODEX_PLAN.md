# H1 执行方案 — OpenAI 原生 Computer Use

> **执行者：本地 Codex（GPT-6）。** 本文件是执行合同，不是执行结果。
> 分支：`experiment/verification-value-benchmark-v1`　Draft PR：#18
> 本方案不修改任何冻结 benchmark 协议。

---

## 0. 角色与边界

你是 **H1 实验执行器**，不是实验设计者，也不是被测模型。

被测模型是 **`gpt-6-astra`**（OpenAI 官方，见 `README.md` 第 15 行）。
你的任务是：用 **OpenAI 原生 Computer Use 协议**把模型接进已有 harness，
执行 A/B/C 三臂，产出 `runs.jsonl` 与 raw trace。

**禁止**：
- 修改 A/B/C 定义、任务文本、成功条件、verifier 语义、gate policy、P0 口径；
- 让 A 看到 verifier、给 B 开 gate、给 C 额外策略提示；
- 把模型自述当 `task_success`；
- 因中途结果调 prompt/阈值/任务/timeout；
- 静默重跑（基础设施错误必须记 `invalid_run=true` + 非空 `invalid_reason`）。

**H0 已完成**（`H0_REPORT.md`，commit `b48e5bf`）：236 项仪器验证 + 审计器。
H1 直接复用 `harness/` 下已有模块，**不要重写第二套语义**。

---

## 1. 已核实的接口事实（务必先读，避免重复踩坑）

以下均为本机实测结论（2026-09-09），**不是推测**：

| # | 事实 | 证据 |
| --- | --- | --- |
| 1 | `computer_use_preview` **被 gpt-6-astra 拒绝** | HTTP 400 `Tool 'computer_use_preview' is not supported with gpt-6-astra-2026-09-03`（Azure 与 OpenAI 上游都拒绝）；`computer_20250124`/`computer_use` 同样 400 |
| 2 | **当前正确工具名是 `{"type": "computer"}`** | OpenAI 官方文档 *Computer use* 页现行示例 |
| 3 | **OpenRouter 会静默丢弃 `computer` 工具** | 请求返回 200，但响应回显 `"tools": []`、`"output": []`；`tool_choice: "required"` 也无法强制 |
| 4 | **OpenRouter 的 Responses API 无状态** | 传 `previous_response_id` → 400 `expected null, received string` |
| 5 | **OpenRouter 拒绝 `computer_call_output`** | HTTP 400 `invalid_union` |
| 6 | 本机**没有 `OPENAI_API_KEY`** | 环境变量为空；`.codex/.env` 无该键；`auth.json` 为 `auth_mode: chatgpt`、`OPENAI_API_KEY: None` |

**结论：原生 Computer Use 必须走 OpenAI 官方 API 直连，不能经 OpenRouter。**
第 3–5 条使 OpenRouter 在任何环节都不可用，这不是配置问题。

---

## 2. 前置条件（阻塞项）

### B1 — 必须提供 `OPENAI_API_KEY`（硬阻塞）

原生 CU 回路要求：
- `tools=[{"type": "computer"}]` 被真实转发；
- `computer_call` / `computer_call_output` 可用；
- 需要多轮状态延续。

OpenRouter 三条都不满足（见第 1 节 3–5）。因此**必须**有可访问 `gpt-6-astra` 的
OpenAI 官方 API key。

**当前状态：缺失。** 本机 Codex 使用 ChatGPT OAuth（`auth_mode: chatgpt`），
该凭据只能供 Codex 自身调用模型，**不能被独立 Python 进程使用**。

在拿到 key 之前，**不要开始正式 H1**。可以先完成第 3–5 节的代码，用
`--dry-run` 验证除模型调用外的全部链路。

### B2 — 需确认 `gpt-6-astra` 是否对该账号开放 computer 工具

官方模型页标注 GPT-6 Astra 的 **Computer use: Supported**，但需用真实 key 验证一次
最小请求。验证脚本见第 9 节 V1。

### B3 — 成本预算（需用户批准）

- gpt-6-astra：输入 $10/M、输出 $50/M；超过 272K 输入 token 单价翻倍。
- 原生 CU 每轮回传一张 1280×800 截图（`detail: "original"`），图像 token 消耗大。
- 单 run 可能 10–30 轮。60 个有效 run（4 task × 3 arm × 5）成本需先估算再冻结。

### B4 — 需确认的两个设计决策

**D1：如何判定 `claimed_success`？**

原生 CU 没有"声明完成"的语义——模型停止调用工具后返回一条消息。
方案：在三臂**完全相同**的工具集里加两个 function 工具：

```python
{"type": "function", "name": "report_success", ...}
{"type": "function", "name": "report_failure", ...}
```

即 `tools = [{"type": "computer"}, report_success, report_failure]`。
三臂工具面逐字相同，满足公平性。C 臂的 gate 在 `report_success` 前拦截。

备选：解析模型最后一条消息文本判断成败——**不推荐**，引入主观解释。

**D2：C 臂 gate 阻断后是否告知模型？**

推荐**静默跳过**：不执行该动作，返回新截图，模型自己观察 UI 未变化。
理由：`H0_DEEPSEEK_PROMPT.md` 禁止"给 C 额外策略提示"，
且真实 enforcement 的表现就是"动作没发生"。

备选：追加一条说明消息——**会构成策略提示，不建议**。

**以上两项请用户确认后再定稿。**

---

## 3. 架构

```
mcp/experiments/verification_value_benchmark_v1/
  ├─ h1_runner.py              新增：H1 执行器（Codex 实现）
  ├─ harness/
  │    ├─ protocol.py          已有：冻结协议只读
  │    ├─ fixture_env.py       已有：Playwright 环境 + 坐标动作 + 命中测试
  │    ├─ cu_agent.py          新增：OpenAI 原生 CU 回路
  │    ├─ gate_policy.py       已有：C 臂 gate（不要复制）
  │    ├─ classifier.py        已有：P0 指标（不要复制）
  │    ├─ schema_validator.py  已有
  │    └─ auditor.py           已有：H1 产物同样要过审计
  └─ analyze_results.py        已有
```

**已就绪的 harness 能力**（`fixture_env.py`，H0 已实现并验证）：

| 方法 | 用途 |
| --- | --- |
| `reset()` / `snapshot()` / `verifier()` / `hidden_judge()` | Harness-only 通道 |
| `click_at(x, y)` | 真实坐标点击，返回 `action_seq` 变化 |
| `mouse_move` / `mouse_drag` | 真实指针动作 |
| `press_key(key)` | 真实按键 |
| `hit_test_business_target(x, y)` | Harness-only：该坐标是否落在业务按钮上 |
| `screenshot_png()` | 截图 |

---

## 4. 原生 Computer Use 回路

### 4.1 请求形态

```python
response = client.responses.create(
    model="gpt-6-astra",
    tools=[{"type": "computer"}, REPORT_SUCCESS, REPORT_FAILURE],
    input=conversation,          # 全量历史（推荐，见 4.4）
    reasoning={"effort": "medium"},   # 跨臂必须一致，运行前冻结
    # store=False,               # 避免服务端留存
)
```

### 4.2 执行模型请求的动作

`computer_call` 携带**有序** `actions` 数组，模型可请求：
`click` / `double_click` / `drag` / `move` / `scroll` / `keypress` / `type` / `wait` / `screenshot`。

**必须逐条执行**（不要批量并行），因为 gate 要在业务 intent 前拦截。
每条 action 映射到 `fixture_env`：

| 模型动作 | harness 调用 |
| --- | --- |
| `click` | `env.click_at(x, y)` |
| `double_click` | `env.click_at(x, y)` ×2（或扩展 dblclick） |
| `move` | `env.mouse_move(x, y)` |
| `drag` | `env.mouse_drag(...)` |
| `scroll` | 需在 `fixture_env` 补 `scroll(x, y, scroll_x, scroll_y)` |
| `keypress` | `env.press_key(normalize_key(keys))` |
| `type` | 需补 `type_text(text)` |
| `wait` | `env.wait_ms(...)` |
| `screenshot` | 不改变 UI，仅回传截图 |

按键名需归一化（官方文档给了映射表）：`ENTER`→`Enter`、`ESC`→`Escape`、
`PAGEUP`→`PageUp`、`CTRL`→`Control` 等。

### 4.3 回传截图

```python
input=[{
    "type": "computer_call_output",
    "call_id": call.call_id,
    "output": {
        "type": "computer_screenshot",
        "image_url": f"data:image/png;base64,{b64}",
        "detail": "original",
    },
}]
```

`call_id` 必须与被执行的那个 call 一致。

### 4.4 多轮状态

两种模式：

| 模式 | 说明 | 建议 |
| --- | --- | --- |
| `previous_response_id` | 服务端保存会话，只发增量 | 省 token，但依赖 `store=true` |
| **全量历史** | 每轮重发完整 `input` 列表 | **推荐**：无服务端状态、raw trace 可完整复现、审计友好 |

若用全量历史，注意 `function_call` / `computer_call` / `reasoning` 回填时
**不要用 `item.model_dump()`**——H0 期间实测该做法被 API 以
`invalid_prompt` 拒绝（reasoning item 缺 `summary` 等）。要显式构造字段。

---

## 5. A/B/C 如何映射到原生 CU

三臂**工具面、模型、参数、任务文本、初始状态、截图能力完全相同**，唯一差异是验证层：

| | A Native | B Passive Verifier | C Verifier + Gate |
| --- | --- | --- | --- |
| 截图 / Computer Use | ✅ | ✅ | ✅ |
| 每 checkpoint 注入 verifier packet | ❌ | ✅ | ✅ |
| gate enforcement | ❌ | ❌ | ✅ |

**Verifier 注入**（B/C）：checkpoint 后把 `verificationBench.verifier()` 的
canonical JSON **原样**作为环境事实加入会话，不加任何建议：

```python
conversation.append({
    "role": "user",
    "content": [{"type": "input_text",
                 "text": f"environment_evidence (verbatim from verificationBench.verifier()):\n{packet}"}],
})
```

B 与 C 必须收到**逐字节等价**的 packet（审计器会跨 run 比对）。

**Checkpoint 判定**：执行动作后比较 `env.snapshot()["action_seq"]`；
变化即产生 checkpoint。A 只记录，B/C 注入 verifier。

---

## 6. Gate 接入点（正式裁决，不得偏离）

> Verification Gate 在第一次业务动作之前不生效。第一次业务 intent 必须允许执行。
> Gate 从第一次业务动作产生 checkpoint 后开始生效，此后在每次 checkpoint 后、
> 后续不可逆 intent 前，以及接受模型 completion claim 前进行检查。
>
> 本 Benchmark v1 测的是 **post-action verification enforcement**，
> 不是 precondition/authorization gate。

实现要点：

1. 维护 `intent_attempted`（首次业务 checkpoint 后置 true）。
2. 执行每个 `click` 前，用 `env.hit_test_business_target(x, y)` 判断是否业务 intent。
3. **仅当** `is_business_intent and intent_attempted` 时咨询 `gate_policy.decide(...)`：
   - `committed` + `duplicate_risk` + 已有 intent → `ACTION_NEW_IRREVERSIBLE_INTENT`
   - `failed`/`unchanged` + `recoverable` → `ACTION_RETRY`
   - 其余 → `ACTION_NEW_IRREVERSIBLE_INTENT`
4. 被拒 → 不执行该动作，`gate_blocks += 1`，写 raw trace，返回新截图（静默，见 D2）。
5. 模型调用 `report_success` 前 → `gate_policy.decide(..., ACTION_FINAL_SUCCESS, ...)`；
   被拒 → 不记录完成声明，`gate_blocks += 1`，让模型继续。

`gate_policy.py` 与 `classifier.py` **必须直接 import**，不得复制常量或另写判定。

---

## 7. 必须记录的内容

`runs.jsonl` 每行须通过 `result.schema.json` 校验（复用 `schema_validator`）。
raw trace 最低字段（`HARNESS_HANDOFF.md`）：

- 模型 ID、模型参数（`reasoning.effort` 等）、harness/客户端版本、执行日期；
- task / arm / run id；
- 每次截图与 Computer Use 动作的时间戳；
- fixture `action_seq`；
- B/C verifier packet 原文；
- C 的 gate 阻断原因；
- 模型**第一次** success claim 的时刻；
- 最终模型文本；
- hidden judge；
- invalid reason（若有）。

**hiddenJudge / authoritative state 永不进入模型输入**——审计器会检查
`model_input` 中是否出现这些串。

---

## 8. 预算

`tasks.json` 已冻结：`max_agent_steps=30`、`max_wall_time_seconds=120`、
`stability_window_ms=3500`。

**注意**：120 秒对原生 CU + 高推理模型可能偏紧（单轮 reasoning 就可能数十秒）。
超出即记 `budget_timeout`，如实记录，**不得**为凑结果放宽阈值。
若实测普遍超时，应报告 blocker 请裁决，而不是改 `tasks.json`。

---

## 9. 执行前验证清单

| # | 检查 | 通过标准 |
| --- | --- | --- |
| V1 | `OPENAI_API_KEY` 可用 | 最小请求返回 200 |
| V2 | `tools=[{"type":"computer"}]` 被转发 | 响应 `"tools"` 非空 |
| V3 | 模型返回 `computer_call` | 首个响应含 `computer_call` |
| V4 | `computer_call_output` + 截图可回传 | 第二轮 200 且模型继续 |
| V5 | 多轮可收敛到 `report_success` | 能在 30 步内结束 |
| V6 | Playwright 后端 | `fixture_env` 可启动并截图 |
| V7 | 四个 fixture 坐标点击可触发 | `action_seq` 变化 |
| V8 | 审计器对 H1 产物 0 findings | `python runner.py audit <dir>` |

V1–V5 全部依赖 B1。**在 B1 解决前，V1–V5 无法进行。**

建议先做**每格 2 次 connection smoke**（4×3×2 = 24 runs），确认无基础设施问题后，
冻结配置，再做每格 ≥5 次正式运行（60 个有效 run）。

---

## 10. 交付物

1. `runs.jsonl` + `summary.json` + `raw/`
2. 全部 false-positive / premature / duplicate / recovery failure 的 raw trace
3. 模型 ID、参数、harness 版本、执行日期
4. invalid runs 及原因
5. 脱敏 H1 报告

Artifacts 输出到 `mcp/experiments/artifacts/verification_value_benchmark_v1/`
（gitignore，**不提交** raw trace / 截图 / key）。

---

## 11. 禁止事项（重申）

- 不给 B/C 更好的截图或更长任务说明；
- 不给 C 额外策略提示；
- 不因 A 连续失败而修改其 prompt；
- 不把 UI 自由文本标成 authoritative evidence；
- 不把模型自述当 `task_success`；
- 不静默重跑；
- 不在 H1 完成前修改生产 Runtime 配合结果；
- 不修改冻结协议文件（`README.md` / `tasks.json` / `arms.json` /
  `result.schema.json` / `HARNESS_HANDOFF.md`）。

---

## 12. 当前状态

| 项 | 状态 |
| --- | --- |
| H0 仪器校准 | ✅ 完成（236/236，0 findings） |
| H1 方案 | ✅ 本文件 |
| `harness/fixture_env.py` 坐标动作 | ✅ 已就绪 |
| `harness/gate_policy.py` / `classifier.py` | ✅ 已就绪，直接复用 |
| `OPENAI_API_KEY` | ❌ **缺失（硬阻塞）** |
| 原生 CU 端到端验证 | ⏸ 等待 B1 |
| 正式 H1 数据 | ⏸ 未产生 |
