---
name: agent-world-executor
description: 弱模型/executor 用的执行版技能(SKILL-executor)。把页面操作写成"如果 X 则调 Y,返回 Z 则做 W"的决策树,弱模型只负责执行不负责推理。适用:Flash 级别模型做多步网页操作。配套通用版 SKILL.md 使用;orchestrator 用强模型走通用版。关键:page_outcome 主标签是每步决策的唯一依据,不再自行合并多信道。
---

# Agent World 执行版技能 (SKILL-executor)

> 本文件只写给**执行者(executor)**——弱模型、上下文有限、工具选择能力弱。
> 与通用版 [SKILL.md](./SKILL.md) 的关系:通用版给 orchestrator(强模型)定原则;本文件给 executor 定**决策树**。
> 原则:**决策由本文件做,你只负责按"如果…则…"执行。**

---

## 〇、总逃生门(必须最先记住)

> **本决策树只覆盖已知模式。遇到任何"上面没写的情况",一律执行:**
> `world_screenshot`(截整页)→ 停止 → 用截图描述你看到了什么 → 请求上级指示。
> **禁止:** 自行发明操作序列 / 反复重试相同路径超过 2 次 / 忽略错误继续前进。

---

## 一、任务类型先归类(task_hint 模板)

开始任何任务前,把用户请求**归类到以下四类之一**,后续所有 world_guide 调用都带这个 hint:

| 类型 | hint 取值(原样使用) | 适用场景 |
|---|---|---|
| `find_and_click` | "找到并点击目标元素" | 点按钮/链接/菜单项 |
| `fill_form` | "填写表单字段并提交" | 注册/登录/搜索填表 |
| `navigate_to` | "进入指定页面或区域" | 跳转/导航/切换标签 |
| `read_content` | "读取页面内容,不操作" | 抓数据/查看信息 |

**规则:** 无法确定类型时选 `read_content` + 截图(不猜,不硬做)。
**说明:** 如果上级(orchestrator)已经给了 hint,直接使用上级给的,不要自己重写。

---

## 二、主决策树(每步必走)

```
第 1 步  打开页面
        → world_open(url, wait_ms=4000)
        → 读返回值 summary 和 status
        → status.page.state = "anomaly" ?
            是 → 走 场景7(反爬/人机验证)
            否 → 继续
        → status.auth.loggedIn = false 且任务需要登录 ?
            是 → 报告"需要登录,请求人工或凭据",停止
            否 → 继续

第 2 步  建立方向感(本步骤不可跳过)
        → world_guide(task=上面四类 hint 之一, max_candidates=6)
        → 读 candidates
        → candidates 为空 ?
            是 → 走 场景7
            否 → 记住最佳候选的 id

第 3 步  定位目标
        → world_entities(role/name/text 过滤, max_results=8)
        → 结果为空 ?
            是 → 扩大过滤条件,再查一次(max_results=30)
            仍为空 → world_screenshot → 停止,上报
        → 取【可交互】目标(interactive=true)的 id

第 4 步  执行操作
        → world_click / world_fill / world_press(用第 3 步的 id)
        → 【立即】读返回值中的 effect.verdict 和 page_outcome:
```

### 操作后判定表(核心,每次操作后必查)

| page_outcome | 含义 | 你的下一步(照做) |
|---|---|---|
| `progressed` | 页面明确前进了 | ✅ 继续下一步 |
| `challenged` | 出现人机验证/阻断 | ⛔ **立即**停止 → world_screenshot → 上报人工(不尝试自动解决) |
| `errored` | 表单校验失败/错误提示 | 🔁 读返回的错误内容 → 修正对应字段 → 重试提交(最多 1 次) |
| `uncertain` | 变化了但性质不明 | ❓ 调 world_state 看 dialogs/forms,最多 1 次;仍不明 → 截图上报 |
| `unchanged` | 没有有效变化 | 🔁 按下方"失败恢复路径"处理一次 |

> 所有动作(world_click/fill/batch_fill/press/click_at/navigate)从阶段 A 起都返回 page_outcome,
> 不存在"没有 page_outcome"的动作;读到 `errored` 时错误信息在卡片 `error` 字段或 `situation.errors`。

### 失败恢复路径(第一步失败后走这里,只走一遍)

```
世界状态存在疑问时的唯一顺序(不自由发挥):
  1. world_state → 看 dialogs
      有弹窗 → 先处理弹窗(场景3)
      无弹窗 → 继续
  2. world_map → 重新定位目标区域
  3. 用新 id 重试一次操作
  4. 仍失败(第二次失败) → world_screenshot → 停止,上报截图+失败原因
```

---

## 三、三个复杂场景的固定处理模式

### 场景 1:输入触发联想下拉(autocomplete)

```
world_fill(id, text, type_delay_ms=30)        ← 必须用打字间隔,不可直接 fill 后提交
  → 读 effect.verdict
  → = no-change ? 失败恢复路径,结束
  → = effected/changed ?
      → world_entities(role="listbox" 或 role="option")
      → 找到 option ?
          是 → world_click(option 的 id)          ← 选中联想项,不是按 Enter
          否 → world_wait(appear, role="listbox", timeout_ms=2000)
                命中 → 再查 option → 点击
                超时 → world_press(id, "ArrowDown") 触发联想 → 再查
  → 最终确认:world_state.forms 里能看到你填的值
【禁止】fill 后直接按 Enter 提交(联想未选择时提交的是空表单)
```

### 场景 3:弹窗/遮罩嵌套

```
任何操作返回 effect 后,先查 page_outcome:
  = challenged ? 停止上报(见判定表)
  = progressed/uncertain ?
      → world_state → dialogs
      → dialogs 有新增 ?
          是 → 只操作弹窗内元素(world_entities 加 bounds 过滤到弹窗范围)
               完成弹窗内交互 → world_state 确认 dialogs 已消失 → 继续
          否 → 继续主流程
【禁止】忽略 dialogs 直接操作主页面的元素(会被遮挡操作失败)
```

### 场景 7:反爬 / 人机验证

```
触发信号(任一):
  - world_open 时 status.page.state = "anomaly"
  - world_guide 返回 candidates 为空
  - 操作后 page_outcome = "challenged"(最可靠,优先信它)

固定动作(与上面判定表一致):
  1. world_screenshot(整页)
  2. 停止
  3. 上报:截图 + "遇到人机验证/反爬,需要人工处理"
  4. 若是 headful 模式(用户在场):等用户处理完,再 world_wait(url 变化) 继续
  5. 若是 headless 模式:任务终止
【禁止】自行尝试破解验证码 / 重试多次 / 假装通过
```

---

## 四、跨页面状态的中断检查(每次 world_open / world_guide / world_state 后)

```
status.auth.loggedIn = false 且 URL 含 login/signin/auth ?
  → 立即停止当前任务(这是任务级中断,不是步骤错误)
  → 报告"已跳转登录页"
  → headful:等人工登录后 world_wait(url 不再在登录页) 继续
  → headless:任务终止
【禁止】尝试自动登录/猜测账号密码
```

---

## 五、工具使用速查(何时用哪个,何时不用)

| 时机 | 用 | 不用 |
|---|---|---|
| 打开新页面后 | world_guide(强制) | world_entities(太早) |
| 找目标元素 | world_entities(过滤) | world_screenshot(最后手段) |
| 操作后判断 | 读返回的 page_outcome/effect | 再调 world_changes(噪声大) |
| 操作结果存疑 | world_state(看 dialogs/forms) | 反复 world_click |
| 想了解页面变化 | world_change_digest | world_changes(原始流,调试才用) |
| 连续失败 2 次 | world_screenshot → 停止上报 | 第三次重试相同路径 |
| 任务完成 | world_close | — |

---

## 六、验收自检(任务完成前过一遍)

- [ ] 所有操作有 page_outcome/effect 证据,没有"我执行了=成功了"的跳步
- [ ] 没有忽略任何 challenged/errored 信号
- [ ] 失败次数 ≤ 2 次/环节,超过即已截图上报
- [ ] 敏感任务(登录/提交/删除)前已请求上级确认或人工在场
- [ ] 任务结束 world_close 已调用

---

## 七、与其他文件的关系

- 通用版:[SKILL.md](./SKILL.md)(orchestrator/强模型用,原则性)
- 设计依据:[docs/探索方向-弱模型复杂场景.md](../docs/探索方向-弱模型复杂场景.md)
- page_outcome 实现:server.py `_build_page_outcome`(progressed/challenged/errored/uncertain/unchanged)