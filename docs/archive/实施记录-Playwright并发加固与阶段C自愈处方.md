# 实施记录 · Playwright 专属工作线程加固与阶段 C 自愈处方 (Recipes & Handoff)

> 日期: 2026-09-03
> 范围: `mcp/server.py` · `mcp/test_protocol.py` · `skills/agent-world/SKILL.md`
> 状态: 已落地并通过全量回归 (32/32 项协议测试 + 23/23 项后果卡测试)

---

## 1. 解决的痛点与技术背景

1. **Playwright 跨线程 greenlet 致命冲突**:
   原版 `call_tool` 使用 `asyncio.run_in_executor(None, ...)` 委托默认全局线程池。Playwright 同步 API 深度依赖 `greenlet` 协程上下文，若快节奏并发请求被分配至不同工作线程，100% 触发 `greenlet.error: Cannot switch to a different thread`，导致进程异常与动作崩溃。
2. **阻断与遮挡场景下弱模型缺乏自愈指引**:
   - 触发人机验证固定遮罩 (`challenged`) 时，卡片此前未提供结构化人工协同指引，`next.suggested` 为空。
   - 存在活动弹窗阻挡外部点击导致 `unchanged` 时，弱模型容易在原地陷入死循环，缺乏明确的处置处方。

---

## 2. 核心改动点

### 2.1 Playwright 专属工作线程调度器 (mcp/server.py)
- 引入 `_pw_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="playwright_worker")`。
- `@server.call_tool()` 全量通过 `_pw_executor` 运行，将所有 Playwright 操作严格约束在同一 OS 线程和 greenlet 上下文中，以极低代价杜绝多线程竞争。

### 2.2 阶段 C: 自愈处方 (Recipes) 与人机交接 (Handoff) 协议 (mcp/server.py)
- **人机交接协议 (`handoff`)**:
  当 `page_outcome == "challenged"` 时，卡片输出结构化 `handoff`:
  ```json
  {
    "required": true,
    "type": "human_challenge",
    "reason": "...",
    "suggested": "页面触发人机验证或固定遮罩,请通知用户在可见窗口协助完成",
    "resume_condition": "challenge_cleared"
  }
  ```
- **弹窗阻挡自愈处方 (`recipes`)**:
  当 `page_outcome == "unchanged"` 且检测到活动弹窗时，自动注入可执行的处方动作列表:
  ```json
  [
    {"action": "world_act", "kind": "press", "id": "el_N", "key": "Escape", "why": "当前存在未关闭的活动弹窗,优先按 Escape 退出"},
    {"action": "world_find", "q": "关闭", "why": "寻找弹窗内的关闭按钮并点击"}
  ]
  ```
  同时在 `next.suggested` 中直接给出友好提示。

### 2.3 测试回归与规范更新
- [`mcp/test_protocol.py`](../mcp/test_protocol.py): 增加 `handoff`、弹窗阻挡自愈 `recipes`、执行处方按键退出、并发调用零异常等 8 项新断言，总项数达 32/32 全过。
- [`skills/agent-world/SKILL.md`](../skills/agent-world/SKILL.md): 补充 `recipes` 与 `handoff` 在决策树中的判定与消费准则。
