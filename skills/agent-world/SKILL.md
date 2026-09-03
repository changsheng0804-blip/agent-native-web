---
name: agent-world
description: Comprehensive Web Navigation, Scraping, Form Filling, and Multi-step Interaction Skill powered by Agent-World MCP (Native Web World: CAD Blueprint + Video Changelog + Status Dashboard).
---

# Agent World 网页交互与自动化技能包

> 基于 `mcp` 的"四层能力（理解/行动/兜底/扩展）+ 状态卡仪表盘"标准化作业规范。
> 核心概念:原生网页世界——把网页实时翻译成智能体可直接理解、查询、操作的结构化空间。

---

## 一、何时触发本技能

当用户任务涉及以下场景时,优先走本技能流程:
1. **复杂动态 SPA 网页交互**:如航班/酒店查询、电商比价、数据检索等。
2. **多步骤表单填写与操作**:点击按钮、下拉选择、输入搜索、按键提交。
3. **需要持久化登录态/账号任务**:OAuth 登录、扫码登录、人机验证码介入。
4. **长流程/多页对比任务**:需要连续追踪页面变化、增量感知而不消耗过量 Token。

---

## 二、标准操作流程 (Playbook)

**默认协议只有 6 个词:open → guide → find → act → outcome → close。**
其余 19 个工具(entities/click/fill/state/changes…)全部标记为 [内部/调试],
仅在需要逃生/深挖时使用;弱模型只学这一条环。

```
1. 打开世界    world_open(url, profile?, headful?, cdp_url?)
               └─→ 立即从返回值读取 summary(元素数/可交互数) 与 status 仪表盘

2. 获取方向    world_guide(task="一句话任务")  ← 打开后必做
               └─→ 返回候选入口(带 el_N 编号与语义名),不读整页

3. 定位构件    world_find(q="名字/编号" | role=…, text=…, interactive=true)
               └─→ 返回 matches[] + ambiguous 标记;匹配多个可交互目标时先 resolve 再动
               └─→ 禁止在 find 里执行动作

4. 执行动作    world_act(kind="click|fill|press|batch_fill", id=el_N, ...)
               └─→ 一个往返一个动作;多个连续动作可用 steps=[...] 聚合执行(等价 world_run)
               └─→ 行动层自动走 Locator -> 坐标手势 -> JS Setter 降级
               └─→ 自带 DOM Diff 与视觉帧差双轨生效报告(visual-effected 捕捉纯 CSS 动效/浮层)

5. 读后果卡    world_act 返回即带 page_outcome 五态主标签;也可 world_outcome() 幂等重读
               └─→ progressed:继续按 guide 推进
               └─→ challenged:停下,报告"被挑战遮罩/验证墙拦截",转 headful 人工或更换路径
               └─→ errored:重试一次或换动作路径(如 click_at/截图)
               └─→ uncertain:有变化但没确认生效,调用 world_state / world_screenshot 复核一次
               └─→ unchanged:未生效,不得重复硬点,换目标或重新 world_guide
               └─→ 必要时(仅逃生)world_changes(since) 游标续读增量事件流

6. 任务收尾    world_close(world_id) 释放浏览器资源并自动持久化 storage_state
```

逃生/调试(不在默认环内):`world_entities` / `world_entity` / `world_map` / `world_layers` /
`world_resolve` / `world_state` / `world_changes` / `world_change_digest` / `world_evidence` /
`world_click` / `world_fill` / `world_batch_fill` / `world_press` / `world_click_at` /
`world_navigate` / `world_wait` / `world_screenshot` / `world_eval` / `world_list`。

---

## 三、统一后果卡 (page_outcome) 判定准则

**每次动作(world_click / world_fill / world_batch_fill / world_press / world_click_at / world_navigate)
都返回同一张后果卡**。弱模型只读首层主标签,不需要拼装 effect/feedback/status:

| 主标签 | 含义 | 下一步 |
|---|---|---|
| `progressed` | 已生效(导航/弹窗/状态翻转/填表验证/视觉变化),含 `situation.type` 与 `why` | 继续任务 |
| `challenged` | 被挑战遮罩/验证墙拦截(检测到新的全屏遮罩或挑战 iframe) | **停下**,优先读取卡片中的 `handoff` 字典通知人工介入 |
| `errored` | 动作抛异常,未获得生效判定(可能已部分生效) | 重试一次或换动作路径 |
| `uncertain` | 有变化但无法确认是否生效(`effect.verdict=changed`) | 用 world_state / world_screenshot 复核一次 |
| `unchanged` | 未观察到任何生效证据(`no-change`),可能是目标错了或已失效 | **优先检查 `recipes` 处方候选**(如有活动弹窗推荐 Escape/关闭),按处方自愈;无处方则换目标,**不得重复硬点** |

卡片结构:`page_outcome / situation / confidence / why / target / action / effect / feedback / status /
page / overlays / sources / next / evidence_seq / changes_seq / world_epoch / recipes / handoff`。
- **自愈处方 `recipes`**: 当存在活动弹窗阻挡点击时,卡片自动提供破局动作候选(如按 Escape 或寻找关闭按钮),模型可直接拾取执行。
- **人机交接 `handoff`**: 当触发反爬挑战或验证码固定遮罩时,提供结构化的人机协作说明与恢复条件。
`el_N` 编号可回查(`world_entity`)、可对质;`world_navigate` 成功后旧编号全部失效(`world_epoch` +1)。
`evidence_seq` 与 `changes_seq` 成对出现,是"这次动作到底发生了什么"的原始凭证。

## 四、状态卡 (Status Dashboard) 判定准则

**默认(6 词协议工具)返回轻量状态卡**(URL/稳定态/登录态/弹窗摘要 + changed 高亮,`"light": true`);
以下情况自动升级为**全量深诊断卡**(含 frames/forms/world 明细):
- `page_outcome` 为 `unchanged` / `uncertain` / `challenged` / `errored`
- 显式传 `verbose=true`

旧工具(entities/click 等 [内部/调试] 工具)始终返回全量卡。重点关注:

| 状态字段 | 检查项与应对策略 |
|---|---|
| `auth.loggedIn` | 为 `true` 表示登录态有效;为 `false` 且任务需账号时,用 `headful=true` 弹窗引导人工登录。 |
| `dialogs` | 包含当前打开的有效可见弹窗。若存在遮挡,先操作弹窗内元素或点击关闭。 |
| `page.state` | • `stable`:页面已稳定,可安全读取。<br>• `loading`:正在渲染,等待或使用 `stabilize_ms`。<br>• `anomaly`:反爬拦截或简化页信号,立即切换 headful 或转视觉兜底。 |
| `changed` | 高亮本轮操作刚刚发生状态翻转的模块(如 `changed.dialogs: true`)。 |
| `forms`(全量卡) | 实时回显当前已填入 values 的表单字段,用于确认输入是否成功生效。 |

---

## 五、行动层与多模态最佳实践

1. **输入联想搜索**:对于输入后需要触发下拉推荐的搜索框,`world_act(kind="fill", ..., type_delay_ms=30)` 模拟真实键盘打字。
2. **多字段表单录入**:`world_act(kind="batch_fill", fields=[...])` 一次性提交多个字段,减少通信往返(逐字段容错,失败会记录在 results);或 `world_act(steps=[...])` 聚合多动作。
3. **按键选择与提交**:`world_act(kind="press", key="Enter")` 或 `world_act(kind="press", key="ArrowDown")` 操作建议项。
4. **原生多模态视觉感知 (SoM 模式)**:当面对密集长列表、复杂卡片流或类似按钮时,调用 `world_screenshot(annotated=True)`,多模态模型可以直接在图上看到每个构件的 `[el_X]` 标签,彻底消除歧义。
5. **遮挡与层级归因**:若操作返回包含 `occlusion` 字段(`covered_by`/`at`/`action`),说明目标被上层元素(弹窗/遮罩)挡住,按 `action` 建议先处理上层再重试;`page_outcome=unchanged` 且带遮挡归因时,`situation.type=occluded`,`why` 会说明被谁挡在哪个坐标——不再是含糊的"没变化"。
6. **CDP 挂载(实验性,独立 profile)**:`world_open(cdp_url="http://localhost:9222")` 可连接已启动调试端口的 Chrome,复用其会话;`world_close` 只断开连接、不关闭浏览器。**安全边界:必须用独立 profile 启动(`--user-data-dir` 指向新目录),暂不连接日常使用的浏览器;CDP 会话下 `world_eval` 已禁用,请走结构化查询。**

