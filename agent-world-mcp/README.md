# Agent World MCP

> 把网页翻译成"Agent 的 CAD 图纸 + 网页视频",以 MCP 标准工具暴露给任何 AI agent。
> 内核:`agent-runtime-extension-v1.1-blueprint`(编号系统 + 图纸查询 + 变更日志)

## 这是什么

一个 MCP 服务器。启动后,AI agent(Claude Code、Cursor、opencode 等)通过 16 个标准工具:

- **打开世界**:`world_open` —— 打开网页并建立原生网页世界(可并行多开,互不干扰,不打扰你的桌面)
- **看图**:`world_entities`(构件清单)/ `world_entity`(构件详情)/ `world_layers`(图层视图)/ `world_resolve`(名字查编号)
- **看视频**:`world_changes` —— 页面变化增量续读(游标),不用每次全量重看
- **动手**:`world_click` / `world_fill` / `world_press` —— 全部编号驱动,无选择器脆弱性
- **验证**:`world_wait`(等待构件出现/消失)/ `world_screenshot`(截图兜底)
- **管理**:`world_list` / `world_close`
- **补充**:`world_navigate`(世界内导航)/ `world_click_at`(坐标点击兜底)/ `world_eval`(世界内 JS 调试)

## 工具速查

| 工具 | 作用 | 心智类比 |
|---|---|---|
| `world_open(url, headful?, profile?, cdp_url?)` | 打开网页,返回世界 ID + 摘要(可开窗/持久化登录态/CDP 日常浏览器挂载) | 摊开图纸 |
| `world_entities({role, text, name, ...})` | 过滤查构件清单 | 构件表(BOM) |
| `world_entity(id)` | 单构件详情(坐标/邻居/区域) | 构件详图 |
| `world_layers()` | 结构/语义/空间/交互统计 | 图纸图层 |
| `world_resolve("combobox.round-trip")` | 名字 → 稳定编号 | 查门牌号 |
| `world_changes(since)` | 增量变更流,游标续读 | 视频帧续读 |
| `world_click(id)` / `world_press(id, key)` | 编号驱动点击(带遮挡检测)/ 按键 | 按编号施工 |
| `world_fill(id, text, type_delay_ms?)` | 编号填表(支持打字间隔模拟触发联想) | 填入内容 |
| `world_batch_fill(fields)` | 批量填多个字段(逐字段容错,单次往返) | 批量填写 |
| `world_wait(appear/disappear, ...)` | 等待构件状态 | 检查施工结果 |
| `world_screenshot(id?)` | 整页/局部截图 | 拍照存档 |
| `world_navigate(url)` | 世界内导航(SPA 换页不用重开) | 图纸翻页 |
| `world_click_at(x, y)` | 视口坐标点击(原生网页世界外元素兜底) | 按坐标施工 |
| `world_eval(expression)` | 世界内执行 JS(调试/特殊查询) | 现场勘察 |
| `world_list` / `world_close` | 管理世界 | 图纸归档 |

## 世界状态卡(仪表盘)

**所有工具返回自动附带 `status` 字段**——登录态/弹窗/页面/表单状态显式暴露,agent 无需猜测:

```json
"status": {
  "auth":    { "loggedIn": true, "via": "cookie:.goofish.com" },
  "dialogs": [{ "id": "dom:div", "name": "number of passengers" }],
  "page":    { "state": "stable", "scrollY": 0, "totalHeight": 3043 },
  "forms":   [{ "id": "el_5", "name": "input.搜索", "value": "hello world" }],
  "world":   { "elements": 461, "changesSeq": 102 },
  "changed": { "dialogs": true, "forms": true }
}
```

| 字段 | 含义 | 推断来源 |
|---|---|---|
| `auth` | 登录态 | 双信号:cookie(server 层,HttpOnly 可读)+ DOM 登录入口 |
| `dialogs` | 打开的弹窗 | 直接 DOM 查询 `role=dialog`/`aria-modal`,可见性过滤(预渲染隐藏弹窗不误报) |
| `page` | 加载/稳定/异常、URL、滚动 | 稳定性=元素数连续两次一致;`anomaly`=原生网页世界 vs 可见 DOM 严重缩水(反爬/异常页信号) |
| `frames` | iframe 感知:逐层 URL/元素数/就绪 | server 层遍历 page.frames(跨域可访问) |
| `forms` | 有值的输入框 | 内核增量维护(受控组件重置值属站点特性,不稳定的场景用视觉兜底) |
| `changed` | 本轮变化高亮 | 对比上次状态 |

**感知盲区修复记录**(实战驱动):
- `world_navigate(url)`:世界内导航(SPA 换页无需关闭重开)
- `world_click_at(x,y)`:视口坐标点击(原生网页世界外元素兜底)
- `world_open` 增加 `stabilize_ms`:等待世界稳定(状态卡 stable)才返回,解决渐进渲染/分层加载的"读太早"问题
- `page.state: anomaly`:反爬/异常页检测(如闲鱼对自动化会话返回简化页或增删循环页时,原生网页世界严重缩水 → 标记)
- **内核观察器饿死修复(2026-08-31)**:observer 原用"每次 mutation 重置 150ms 防抖"设计,持续变更页面(懒加载/轮播/广告刷新,如 Booking)会让 `onChange` 永远不触发 → 原生网页世界停更。实测 Booking 上 world 停滞在 678 个、forceRefresh 却抓到 2426 个。改为**累积式防抖 + maxWait(1000ms)兜底**:mutation 累积到 pending,变更停止 150ms 后处理;若持续变更导致防抖被不断重置,最多 1000ms 强制 flush 一次。修复后捕获率 28% → 98%。涉及 `content/observer.js`,改后需重新构建 `all-in-one.js`。
- **anomaly 口径修复(2026-08-31)**:anomaly 检测原用"裸可见 DOM"计数(`querySelectorAll('*')`),而原生网页世界 scanner 会过滤装饰标签(br/svg/path/script 等)+ 小元素。重型 SPA 合法地"DOM 多、模型少" → 误报反爬。改为与 scanner 同口径(排除装饰标签 + <3px)后再比较。涉及 `server.py` 状态卡。
- **可见性过滤增强:识别难识别内容(2026-08-31)**:scanner 原只过滤 display/visibility/opacity 三种结构性隐藏 + 小元素,对"肉眼难识别但仍占位/有文本"的内容(同色文字 / 绝对定位脱出视口 / font-size:0 / 大幅负 text-indent / aria-hidden)判断不足,这些内容会以失真的形态混进原生网页世界。新增 `engine/visibility.js#isPseudoHidden` 补五类判断(同色文字/绝对定位脱出视口上方左侧/font-size:0/大幅负 text-indent/aria-hidden 祖先链),scanner + 状态卡 dialogs 统一接入;observer 补 aria-hidden 属性监听,运行时变隐藏的元素从世界移除(含子树)。修复后五类内容全被识别过滤,正常元素不误伤,动态"先可见后隐藏"的内容也会被及时移除。涉及 `engine/visibility.js`、`engine/scanner.js`、`content/observer.js`、`content/runtime.js`,改后重建 `all-in-one.js`。

操作类工具(`world_click`/`world_fill`/`world_press`)执行后自动刷新状态卡(等待渲染),返回即见操作结果。

## 登录态与人工介入(headful + profile)

`world_open` 支持两个参数,解决登录/验证码/真人确认场景:

- **`profile: "名称"`**:使用持久化登录态(独立浏览器 profile 目录 `profiles/<名称>/`)。同一名称复用 cookie/会话——登录一次,长期有效。适合 OAuth、需要账号的任务。
- **`headful: true`**:弹出可见浏览器窗口。配合 profile 使用:agent 打开页面 → 你在弹出的窗口里完成登录/验证码 → agent 的原生网页世界自动同步,继续操作。

**会话保存机制(双保险)**:
1. Chromium profile 目录持久化(持久 cookie)
2. `world_close` 时自动导出完整会话状态到 `profiles/<名称>/storage_state.json`(**含 session cookie**),重开同 profile 时自动恢复——解决"扫码/快速登录只种 session cookie、关闭浏览器即失效"的问题

典型登录流程:

```
world_open(url, profile="login-淘宝", headful=true)   # 弹窗
  → agent 检测到登录页,提示你完成登录
  → 你在窗口里登录
  → agent 原生网页世界同步到登录后状态,继续任务
  → world_close 自动导出会话
  → 以后同 profile 打开,免登录(无需再弹窗)
```

注意:登录/验证码场景**必须弹窗**(headful),否则无人能替你完成人工环节。部分网站(如闲鱼)会检测 headless 指纹并拒绝访问("非法访问"提示),headful 模式可规避。

## CDP 挂载(实验性,⚠️ 安全须知)

`world_open(cdp_url="http://localhost:9222")` 可连接一个**已启动调试端口**的 Chrome(复用其已登录会话/已打开页面)。**注意:只支持连接独立 profile 启动的 Chrome;连接日常使用的浏览器场景已降级为暂不推荐(见下),安全设计另行立项。**

```
# 先手动启动带调试端口的 Chrome(必须用独立 profile,不要用日常 profile):
chrome.exe --remote-debugging-port=9222 --user-data-dir=/tmp/cdp-profile --no-first-run

# 再让 agent 挂载:
world_open(url, cdp_url="http://localhost:9222")
```

**行为约定(安全边界)**:
- `world_close` 对 CDP 连接**只断开、不关闭浏览器进程**——你手动启动的 Chrome 不会被误关
- CDP 世界**不导出** `storage_state`(会话属于你的浏览器,不落盘)
- 注入失败时同样只断开,不触碰浏览器
- **CDP 会话下 `world_eval` 已禁用**——IPI 攻防实测确认任意 JS 可绕过 visibility 过滤层直接读整页文本/凭据,故 CDP 场景强制走结构化查询(`world_entities`/`world_entity`)

**⚠️ 安全须知(实验特性,默认不推荐)**:
- **CDP 调试端口无鉴权**——只要端口开着,本机任何进程/恶意网页都能连上并取得**完整浏览器控制权**(cookie/凭据/下载/任意页面)。用完务必关闭端口。
- **必须绑定 localhost**——禁止 `--remote-debugging-address=0.0.0.0`(会暴露到局域网)。
- **必须用独立 profile**(`--user-data-dir` 指向新目录),**绝不连你日常使用的浏览器 profile**。
- 连接期间,世界模型脚本运行在该 Chrome 的会话里,可读写当前页面——只对可信站点/可信任务使用。
- 安全性验证:`python test_cdp.py`(启动临时独立 Chrome → 挂载 → 操作 → 关闭后断言浏览器进程仍存活)。

**🚫 暂不推荐:连接日常使用的浏览器**。复用日常登录态的完整安全设计(CDP 端口鉴权、域白名单、操作审计、用户显式授权确认)体量已超出本仓库范围,作为独立方向评估。

## 行动层策略(locator 优先,双重降级)

操作类工具(world_click/fill/press)采用三层策略,兼顾"原生能力"与"原生网页世界兜底":

1. **Playwright locator**(默认):根据原生网页世界元素信息(页面 id → placeholder → ARIA role+可访问名 → 文本)自动构建语义定位,自带自动等待、可见性检查、清晰错误诊断
2. **坐标鼠标手势**(降级):locator 失败时,用原生网页世界实时 rect + scrollIntoView + 真实鼠标事件
3. **JS 注入**(fill 再降级):React 受控组件 setter + 覆盖层自动切换

工具返回的 `method` 字段标明实际走哪条路径(`locator` / `mouse-gesture` / `js-setter`),agent 可据此判断可靠性。

**实战修复:fill 覆盖层验证(2026-08-31)**。Playwright 的 `locator.fill()` 只要求元素"可见可编辑",**不检测遮挡**。SPA 对话框(如 Google Flights 点出发地后弹出"输入您的出发地")会新建一个可见输入框副本、覆盖原输入框——此时 `world_fill` 会"静默成功"地把值填进被覆盖的旧框,可见输入框实际为空。修复:locator fill 成功后用 `elementFromPoint` 验证文本是否落在"可见且未被覆盖"的输入框,未验证到则判定失败、降级到 js-setter(自带覆盖层切换,自动改填顶层可见输入框)。

## 实时闭环反馈(操作→结果,2026-08-31)

传统浏览器操作靠"被动等":提交后只能轮询或延时截图猜结果。原生网页世界基于 runtime 实时性,把"操作→结果"的因果直接暴露给 agent:

1. **`world_changes` 变更可读化**:内核变更事件补 `semantic` 字段(remove 事件也在删除前捕获 name/semantic);server 层对每条事件打 `importance` 分级(high/medium/low,依据事件类型 × 语义角色),并生成 `digest` 人话摘要(`{summary, counts, highlights}`)——agent 读一眼就知道"新增 43 个构件、关键: 弹窗 dialog.number-of-passengers",不必翻原始事件流。
2. **`world_click` 返回 `effect` 生效报告**(点击验证闭环):点击前冻结目标空间区域(目标 bounds ±200px)→ 点击后轮询区域 diff(有变化即停,最多 2.5s)→ 生成:
   ```json
   "effect": {
     "verdict": "effected",            // effected / changed / no-change
     "confidence": "high",             // high / medium / high(no-change 也是 high 相对可靠)
     "why": "目标区域出现关键构件: 弹窗 dialog.number-of-passengers、按钮 button.add-adult",
     "observed": [{"type":"add","id":"el_595","semantic":"dialog","name":"dialog.number-of-passengers"}],
     "region_changed": {"new": 64, "gone": 57}
   }
   ```
   **设计依据(实测)**:重型 SPA(Google Flights)一次点击会整体重渲染 DOM(新增 108/移除 851/更新 946),全页 diff 全是噪声;且元素 ID 随重渲染失效(WeakMap 重建),不能靠 ID/邻居。改为"目标空间区域 ±200px 点击前后 diff",真信号(乘客面板 `dialog.number-of-passengers` 及 49 个面板构件)从噪声中干净分离。**负例不误报**:点击无副作用元素判 `no-change`。
   - 证据窗口:轮询"区域有变化即停"(不傻等满),最多 2.5s
   - 判定:区域新增关键交互构件(dialog/button/menu/option 等)→ `effected/high`;仅 URL 变化 → `effected/high`(导航类);区域有变化无关键构件 → `changed/medium`;无变化 → `no-change`
   - 世界出证据 + 分级置信度,**最终判断权留给 agent**(避免把页面自身波动误判为操作失败)

3. **`world_wait` 事件驱动(替代轮询)**:内核 `AgentRuntime.waitFor()` 注册 waiter,MutationObserver 每次 flush(`handleMutation`)后检查条件、命中即 resolve;server 端用 `page.evaluate` 的 Promise await 机制等待,彻底去掉旧的 `time.sleep(0.3)` 轮询循环。实测:条件已满足 0.04s 返回、动态出现 0.03s 命中、消失 0.16s、超时精确(2s 超时 2.04s 返回)。返回带 `driven: event|timeout` 标明路径。

**语义摘要 + 重要性加权是闭环的基础设施**:闭环反馈不能把原始 diff 全塞给 agent(信息洪水、上下文爆炸),"快"靠"给得更少但更准"。实测路线:全页 digest(变更可读化)+ 空间作用域 effect(操作生效报告),两级配合。

## 安装与配置

### 前置

```bash
pip install mcp playwright
playwright install chromium
```

### 接入 opencode

编辑 `~/.config/opencode/opencode.jsonc`(Windows:`C:\Users\<你>\.config\opencode\opencode.jsonc`),添加:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "agent-world": {
      "type": "local",
      "command": ["python", "F:/成果库/Agent 友好插件/agent-world-mcp/server.py"],
      "enabled": true
    }
  }
}
```

重启 opencode 生效。之后 agent 会看到 `world_*` 工具。

### 接入 Claude Code / Cursor(其他 MCP 客户端)

按各自 MCP 配置规范,注册命令:`python <本目录>/server.py`(stdio 模式)。

### 手动测试

```bash
python test_official.py   # 官方客户端全链路测试
```

## 目录结构

```
agent-world-mcp/
├── server.py          # MCP 服务器(核心)
├── profiles/          # world_open 的 profile 持久化登录态目录
├── screenshots/       # world_screenshot 输出目录
├── probe_site.py      # 通用站点探针:对新站点做标准体检(摘要/状态卡/图层/交互/变更流)
├── probe_fill.py      # 行动层 fill 探针:click+fill+DOM 验证+模型验证(URL/目标/文本)
├── test_official.py   # 官方客户端全链路:握手/工具列表/world_open
├── test_click.py      # 点击乘客按钮 -> 面板弹出 -> Adults 元素出现
├── test_fill.py       # 填出发地 Tokyo -> 建议列表出现
├── test_action_layer.py  # 行动层:locator 优先 + 覆盖层验证 + world_press
├── test_enhancements.py  # 进阶增强:逐字打字/批量填表/遮挡诊断(本地夹具)
├── test_cdp.py       # CDP 挂载安全测试:独立 Chrome + 关闭后浏览器进程存活断言
├── test_value.py      # value 入图:fill 后原生网页世界可见输入框值
├── test_profile.py    # headful + profile:登录态持久化验证
├── test_compare.py    # profile vs 普通 launch 的原生网页世界对比
├── test_frames.py     # frame 感知 + anomaly + navigate + click_at
├── test_status.py     # 世界状态卡:auth/dialogs/page/forms/changed
├── test_eval.py       # world_eval:只读查询/函数表达式/截断/错误
├── test_ipi_filter.py # IPI 伪隐藏过滤:VEC_4~VEC_8 阻断 + 对照不误伤 + 动态时序移除
├── test_change_digest.py  # 变更可读化:world_changes digest+importance(本地动态页+GF)
├── test_click_effect.py   # 点击生效报告:正例 GF 面板 effected/high + 负例 no-change
├── test_wait_event.py     # world_wait 事件驱动:已满足/动态出现/消失/超时兜底
└── test_gf_final.py   # 正常站点(GF)上 frames/anomaly/navigate 冒烟

(monorepo 根)
├── test_fixtures/dyn.html     # 本地动态测试页夹具(供 test_enhancements/test_status 用)
└── skills/agent-world/SKILL.md # Agent World 标准化技能包(四层能力 Playbook)
```

## 实战验证矩阵(2026-08-31)

| 站点 | 特点 | 原生网页世界捕获 | 状态卡 | 行动层 |
|---|---|---|---|---|
| Wikipedia | 简单静态 | 154/189 (81%) | stable, 无 anomaly | click=locator ✅ |
| Google Flights | 重型 SPA+覆盖层 | 461+ | stable | click/fill/press 三层降级 ✅ |
| Booking.com | 重型 SPA+懒加载 | 1843/1883 (**98%**) | stable | click=mouse-gesture ✅ |
| Amazon | 电商重型 | 1328/1386 (**96%**) | stable, 无 anomaly | click=locator, fill=js-setter ✅ |
| GitHub | SPA 导航 | 649/658 (**98.6%**) | stable | click=locator ✅ |
| Stack Overflow | 表单+弹窗 | 957/957 (**100%**) | loading(持续渲染), 无 anomaly | fill=locator-fill ✅ |
| 百度 | 中文搜索 | 136/155 (88%) | loading | fill=locator-fill ✅ |
| BBC | 重型新闻页+多 iframe | 926/927 (**99.9%**) | loading | click=mouse-gesture ✅ |
| OpenStreetMap | 地图+iframe | 110/122 (90%) | stable | click=locator ✅ |
| Reddit | 反爬拦截页 | 14/14 | **anomaly 不误报** ✅ | — |
| eBay | 反爬拦截页 | 14/14 | **anomaly 不误报** ✅ | — |

**实战要点**:
- 反爬拦截页(Reddit/eBay 的简化页)会正常建模,且 **anomaly 口径修复后不再误报**(模型与 DOM 一致就不算缩水)
- 百度搜索框语义是 `textbox`(名字来自热门词占位符,易变)——定位输入框应**用 role 查询**(`role=textbox/input/combobox/searchbox`)而非猜名字
- 观察器饿死修复后,持续变更站点(Booking/Amazon)原生网页世界能追上 DOM(捕获率 28%→96%+)

## 依赖说明

- 读取 `../agent-runtime-extension-v1.1-blueprint/all-in-one.js` 作为注入内核
- headless Chromium,每次 world_open 一个独立浏览器实例(并行不串扰)
- 截图保存本地,不随 MCP 传输图片(节省上下文,路径返回给 agent)

## 已知限制

- 页面拒绝注入时(极少数反自动化站点)world_open 会报错
- 每个世界约 200MB 内存,并行数量受机器内存限制
- 无 profile 的会话不持久化登录态(需要持久化登录态请用 profile,见上文「登录态与人工介入」)

## 测试脚本

```bash
python probe_site.py <url> [wait_ms] [stabilize_ms]  # 通用站点探针:新站点体检(摘要/状态卡/图层/交互/变更流)
python probe_fill.py <url> <目标> <文本> [wait_ms]    # fill 探针:click+fill+DOM/模型双验证
python test_official.py       # 官方客户端全链路:握手/工具列表/world_open
python test_click.py          # 点击乘客按钮 -> 面板弹出 -> Adults 元素出现
python test_fill.py           # 填出发地 Tokyo -> 建议列表(option.tokyo-japan)出现
python test_action_layer.py   # 行动层:locator 优先 + 覆盖层验证降级 + world_press
python test_enhancements.py   # 进阶增强:逐字打字/批量填表(容错)/遮挡诊断(本地夹具,不依赖外网)
python test_cdp.py            # CDP 挂载安全:独立临时 Chrome + 关闭后浏览器存活断言(安全回归)
python test_value.py          # value 入图:fill 后原生网页世界可见输入框值
python test_profile.py        # headful + profile:登录态持久化验证
python test_compare.py        # profile vs 普通 launch:原生网页世界对比
python test_frames.py         # frame 感知 + anomaly + navigate + click_at
python test_status.py         # 世界状态卡:auth/dialogs/page/forms/changed(本地夹具)
python test_eval.py           # world_eval:JS 查询/截断/错误处理
python test_ipi_filter.py     # IPI 伪隐藏过滤:VEC_4~VEC_8 阻断 + 对照不误伤 + 动态时序(本地夹具)
python test_change_digest.py  # 变更可读化:world_changes digest + importance(本地动态页 + GF)
python test_click_effect.py   # 点击生效报告:正例 GF 面板 effected/high + 负例 no-change 不误报
python test_wait_event.py     # world_wait 事件驱动:已满足/动态出现/消失/超时兜底
python test_gf_final.py       # Google Flights 冒烟:frames/anomaly/navigate 无异常
```