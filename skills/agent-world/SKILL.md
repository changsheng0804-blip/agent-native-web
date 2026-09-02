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

```
1. 打开世界    world_open(url, profile?, headful?, cdp_url?)
               └─→ 立即从返回值读取 summary(元素数/可交互数) 与 status 仪表盘

2. 观察结构    world_layers() / world_entities(过滤条件)
               └─→ 定位目标构件编号(如 el_128)与语义名字(如 combobox.where-from)

3. 执行操作    world_click(id) / world_fill(id, text, type_delay_ms?) / world_batch_fill(...) / world_press(id, key)
               └─→ 行动层自动走 Locator -> 坐标手势 -> JS Setter 降级
               └─→ 自带 DOM Diff 与视觉帧差双轨生效报告(visual-effected 捕捉纯 CSS 动效/浮层)

4. 验证变化    读操作返回的 page_outcome 五态主标签 + 统一后果卡
               └─→ progressed:继续按 guide 推进
               └─→ challenged:停下,报告"被挑战遮罩/验证墙拦截",转 headful 人工或更换路径
               └─→ errored:重试一次或换动作路径(如 click_at/截图)
               └─→ uncertain:有变化但没确认生效,调用 world_state / world_screenshot 复核一次
               └─→ unchanged:未生效,不得重复硬点,换目标或重新 world_guide
               └─→ 必要时调用 world_changes(since) 游标续读增量事件流
               └─→ 或调用 world_wait(mode="appear", name="...") 等待预期构件渲染

5. 疑难兜底    若状态卡或构件查询无响应,或面对高密度排版/Canvas/图表黑盒:
               └─→ 调用 world_screenshot(annotated=True) 获得带 [el_X] 编号标注的 Set-of-Mark 图像与原生 Base64 数据
               └─→ 原生多模态模型可直接图文对照精准定位或结合 world_click_at 进行坐标操作

6. 任务收尾    world_close(world_id) 释放浏览器资源并自动持久化 storage_state
```

---

## 三、统一后果卡 (page_outcome) 判定准则

**每次动作(world_click / world_fill / world_batch_fill / world_press / world_click_at / world_navigate)
都返回同一张后果卡**。弱模型只读首层主标签,不需要拼装 effect/feedback/status:

| 主标签 | 含义 | 下一步 |
|---|---|---|
| `progressed` | 已生效(导航/弹窗/状态翻转/填表验证/视觉变化),含 `situation.type` 与 `why` | 继续任务 |
| `challenged` | 被挑战遮罩/验证墙拦截(检测到新的全屏遮罩或挑战 iframe) | **停下**,报告人类或换路径 |
| `errored` | 动作抛异常,未获得生效判定(可能已部分生效) | 重试一次或换动作路径 |
| `uncertain` | 有变化但无法确认是否生效(`effect.verdict=changed`) | 用 world_state / world_screenshot 复核一次 |
| `unchanged` | 未观察到任何生效证据(`no-change`),可能是目标错了或已失效 | 换目标或重新 world_guide,**不得重复硬点** |

卡片结构:`page_outcome / situation / confidence / why / target / action / effect / feedback / status /
page / overlays / sources / next / evidence_seq / changes_seq / world_epoch`。
`el_N` 编号可回查(`world_entity`)、可对质;`world_navigate` 成功后旧编号全部失效(`world_epoch` +1)。
`evidence_seq` 与 `changes_seq` 成对出现,是"这次动作到底发生了什么"的原始凭证。

## 四、状态卡 (Status Dashboard) 判定准则

每次 MCP 工具调用均返回最新的 `status` 字段,重点关注:

| 状态字段 | 检查项与应对策略 |
|---|---|
| `auth.loggedIn` | 为 `true` 表示登录态有效;为 `false` 且任务需账号时,用 `headful=true` 弹窗引导人工登录。 |
| `dialogs` | 包含当前打开的有效可见弹窗。若存在遮挡,先操作弹窗内元素或点击关闭。 |
| `forms` | 实时回显当前已填入 values 的表单字段,用于确认输入是否成功生效。 |
| `page.state` | • `stable`:页面已稳定,可安全读取。<br>• `loading`:正在渲染,等待或使用 `stabilize_ms`。<br>• `anomaly`:反爬拦截或简化页信号,立即切换 headful 或转视觉兜底。 |
| `changed` | 高亮本轮操作刚刚发生状态翻转的模块(如 `changed.dialogs: true`)。 |

---

## 五、行动层与多模态最佳实践

1. **输入联想搜索**:对于输入后需要触发下拉推荐的搜索框,设置 `type_delay_ms: 30` 模拟真实键盘打字。
2. **多字段表单录入**:优先使用 `world_batch_fill` 一次性提交多个字段,减少通信往返(逐字段容错,失败会记录在 results)。
3. **按键选择与提交**:使用 `world_press(id, "Enter")` 或 `world_press(id, "ArrowDown")` 操作建议项。
4. **原生多模态视觉感知 (SoM 模式)**:当面对密集长列表、复杂卡片流或类似按钮时，调用 `world_screenshot(annotated=True)`，多模态模型可以直接在图上看到每个构件的 `[el_X]` 标签，彻底消除歧义。
5. **遮挡与层级提示**:若操作返回包含 `obscured_note`,说明目标上方有蒙层或对话框,优先处理上层元素。
6. **CDP 挂载(实验性,独立 profile)**:`world_open(cdp_url="http://localhost:9222")` 可连接已启动调试端口的 Chrome,复用其会话;`world_close` 只断开连接、不关闭浏览器。**安全边界:必须用独立 profile 启动(`--user-data-dir` 指向新目录),暂不连接日常使用的浏览器;CDP 会话下 `world_eval` 已禁用,请走结构化查询。**

