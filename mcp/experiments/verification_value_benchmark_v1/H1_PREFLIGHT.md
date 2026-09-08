# H1 Preflight — 调用条件与执行路径

> 只读检查报告。**未产生任何正式 H1 数据。** 正式运行需显式 `--confirm-live-model`。

- 检查日期：2026-09-09
- 基线 HEAD：`b48e5bf1abd68fa91e821e5eb927100104c39bdc`
- 目标模型：`openai/gpt-6-astra`（OpenRouter 路由）

## 0. 结论

**具备调用条件（经 OpenRouter 中介），但不具备 OpenAI 官方 Responses API + 原生 Computer Use 直连条件。**

关键结论：**GPT-6 Astra 明确拒绝原生 `computer_use_preview` 工具**，
必须由 Harness 用 function tools 中介执行真实鼠标/键盘事件。
这符合本 benchmark 的设计——`arms.json` 的 `model_capabilities` 是
`screenshot` + `computer_use`，由 Harness 提供，并非要求模型内置原生 CU。

## 1. 逐项检查结果

| 项 | 状态 | 证据 |
| --- | --- | --- |
| 冻结协议自检 | ✅ | tasks=4 arms=A/B/C stability_ms=3500 |
| `OPENROUTER_API_KEY` | ✅ | 已设置 |
| `OPENAI_API_KEY` | ❌ | 未设置 |
| openai SDK | ✅ | 2.43.0，`Responses` 可用 |
| 模型 `openai/gpt-6-astra` | ✅ | 实测调用成功，`tokens_in=349` |
| Responses API 端点 | ✅ | OpenRouter `/api/v1/responses` 返回 200 |
| 视觉输入（截图） | ✅ | 64×64 图正确识别为 "Red" |
| function tool 调用 | ✅ | `mouse_click({"x":32,"y":32})` 正确返回 |
| 多轮 function_call 往返 | ✅ | 2 轮内到达 `report_success` |
| `reasoning.effort` 参数 | ✅ | minimal/low/medium/high 均可（Responses API 与 `extra_body` 两条路径） |
| `seed` / `temperature` | ✅ | 可用（可复现性控制） |
| Playwright 浏览器后端 | ✅ | chromium 可启动，截图 12243 bytes |
| 网络可达 | ✅ | openrouter.ai / openai.com / github.com |

## 2. 不满足的条件（关键）

### 2.1 原生 Computer Use 被模型拒绝

```
Tool 'computer_use_preview' is not supported with gpt-6-astra-2026-09-03.
```

- `computer_use_preview` → 400（Azure 与 OpenAI 两个上游都拒绝）
- `computer_20250124` / `computer_use` / `computer` → 400
  （`requires a matching Responses skin to forward verbatim`）
- `gpt-6-astra-pro` 同样拒绝

**含义**：无法使用 OpenAI 原生 Computer Use 回路
（模型直接产出 `computer_call` + `action`，由 OpenAI 侧驱动虚拟桌面）。
本 benchmark 的 A/B/C 三臂都通过 Harness 中介：
模型输出 function call → Harness 在 Playwright 上执行真实鼠标事件 → 截图回填。

**这不是缺陷**：`arms.json` 只要求模型可见 `screenshot` + `computer_use` 能力，
三臂工具面逐字相同即满足公平性。原生 CU 不是本实验的必需条件。

### 2.2 无 OpenAI 官方直连凭据

- `OPENAI_API_KEY` 未设置；直连 `api.openai.com` 返回 401。
- 所有调用经 OpenRouter（`OPENROUTER_API_KEY`）。
- 影响：模型 ID 需写成 `openai/gpt-6-astra`（OpenRouter 命名空间），
  不是官方 `gpt-6-astra`。跨臂只要统一使用同一字符串即可。

### 2.3 模型版本可能漂移

上游报 `gpt-6-astra-2026-09-03`。OpenRouter 会滚动上游版本，
需在 manifest 记录实际返回的 `model` 字段以便追溯。

## 3. 建议的执行路径

```
h1_runner.py
  ├─ harness/protocol.py      冻结协议只读（任务文本/成功条件/阈值）
  ├─ harness/llm_agent.py     模型侧：Responses API + function tools
  │    └─ OpenAI SDK → https://openrouter.ai/api/v1
  │         model=openai/gpt-6-astra, reasoning={"effort":"medium"}
  ├─ harness/fixture_env.py   浏览器侧：Playwright chromium 1280×800
  │    └─ 真实 mouse.click(x,y) / keyboard.press / screenshot
  ├─ harness/gate_policy.py   C 臂 gate（与 H0 同一模块，语义未变）
  └─ harness/classifier.py    P0 指标（与 H0 同一模块，口径未变）
```

**决策回路**：

1. 截图 → 模型
2. 模型返回 function call（如 `mouse_click`）
3. Harness 命中测试：坐标是否落在业务按钮上 → 判定是否「不可逆业务 intent」
4. 若是业务 intent 且**已发生过** intent → 咨询 gate（C 臂）
5. 执行真实鼠标事件
6. 若 `action_seq` 变化 → checkpoint → B/C 注入 verifier packet
7. 新截图 + 工具结果回填模型 → 下一轮

**Gate 时机**（正式裁决，已落实到 `h1_runner.py`）：
第一次业务动作之前不生效，第一次业务 intent 必须允许执行；
此后在每次 checkpoint 后、后续不可逆 intent 前、接受 completion claim 前检查。

## 4. 正式运行前需确认

| # | 事项 | 说明 |
| --- | --- | --- |
| 1 | 成本预算 | gpt-6-astra 约 $10/M input、$50/M output；每 run 多轮截图，60 runs 成本需先估算 |
| 2 | 每格次数 | README 建议先每格 2 次 smoke，再冻结配置每格 ≥5 次 |
| 3 | `reasoning_effort` 取值 | 需在正式运行前**冻结**（建议 medium），跨臂一致 |
| 4 | 是否允许原生 CU 缺失 | 需确认「Harness 中介 CU」符合实验意图（我的判断：符合） |
| 5 | 预算上限 | `max_agent_steps=30`、`max_wall_time_seconds=120` 已由 tasks.json 冻结 |

## 5. 本次未做的事

- 未执行任何正式 H1 run（`--confirm-live-model` 未用于写 artifacts）
- 未修改任何冻结协议文件
- 未修改生产 MCP / extension
- 未 merge PR #18，未转 Ready for Review
