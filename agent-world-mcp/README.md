# Agent World MCP

> 把网页翻译成"Agent 的 CAD 图纸 + 网页视频",以 MCP 标准工具暴露给任何 AI agent。
> 内核:`agent-runtime-extension-v1.1-blueprint`(编号系统 + 图纸查询 + 变更日志)

## 这是什么

一个 MCP 服务器。启动后,AI agent(Claude Code、Cursor、opencode 等)通过 12 个标准工具:

- **打开世界**:`world_open` —— 打开网页并建立世界模型(可并行多开,互不干扰,不打扰你的桌面)
- **看图**:`world_entities`(构件清单)/ `world_entity`(构件详情)/ `world_layers`(图层视图)/ `world_resolve`(名字查编号)
- **看视频**:`world_changes` —— 页面变化增量续读(游标),不用每次全量重看
- **动手**:`world_click` / `world_fill` —— 全部编号驱动,无选择器脆弱性
- **验证**:`world_wait`(等待构件出现/消失)/ `world_screenshot`(截图兜底)
- **管理**:`world_list` / `world_close`

## 工具速查

| 工具 | 作用 | 心智类比 |
|---|---|---|
| `world_open(url, headful?, profile?)` | 打开网页,返回世界 ID + 摘要(可开窗/持久化登录态) | 摊开图纸 |
| `world_entities({role, text, name, ...})` | 过滤查构件清单 | 构件表(BOM) |
| `world_entity(id)` | 单构件详情(坐标/邻居/区域) | 构件详图 |
| `world_layers()` | 结构/语义/空间/交互统计 | 图纸图层 |
| `world_resolve("combobox.round-trip")` | 名字 → 稳定编号 | 查门牌号 |
| `world_changes(since)` | 增量变更流,游标续读 | 视频帧续读 |
| `world_click(id)` / `world_fill(id, text)` / `world_press(id, key)` | 编号驱动操作 | 按编号施工 |
| `world_wait(appear/disappear, ...)` | 等待构件状态 | 检查施工结果 |
| `world_screenshot(id?)` | 整页/局部截图 | 拍照存档 |
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
| `page` | 加载/稳定/异常、URL、滚动 | 稳定性=元素数连续两次一致;`anomaly`=世界模型 vs 可见 DOM 严重缩水(反爬/异常页信号) |
| `frames` | iframe 感知:逐层 URL/元素数/就绪 | server 层遍历 page.frames(跨域可访问) |
| `forms` | 有值的输入框 | 内核增量维护(受控组件重置值属站点特性,不稳定的场景用视觉兜底) |
| `changed` | 本轮变化高亮 | 对比上次状态 |

**感知盲区修复记录**(实战驱动):
- `world_navigate(url)`:世界内导航(SPA 换页无需关闭重开)
- `world_click_at(x,y)`:视口坐标点击(世界模型外元素兜底)
- `world_open` 增加 `stabilize_ms`:等待世界稳定(状态卡 stable)才返回,解决渐进渲染/分层加载的"读太早"问题
- `page.state: anomaly`:反爬/异常页检测(如闲鱼对自动化会话返回简化页或增删循环页时,世界模型严重缩水 → 标记)

操作类工具(`world_click`/`world_fill`/`world_press`)执行后自动刷新状态卡(等待渲染),返回即见操作结果。

## 登录态与人工介入(headful + profile)

`world_open` 支持两个参数,解决登录/验证码/真人确认场景:

- **`profile: "名称"`**:使用持久化登录态(独立浏览器 profile 目录 `profiles/<名称>/`)。同一名称复用 cookie/会话——登录一次,长期有效。适合 OAuth、需要账号的任务。
- **`headful: true`**:弹出可见浏览器窗口。配合 profile 使用:agent 打开页面 → 你在弹出的窗口里完成登录/验证码 → agent 的世界模型自动同步,继续操作。

**会话保存机制(双保险)**:
1. Chromium profile 目录持久化(持久 cookie)
2. `world_close` 时自动导出完整会话状态到 `profiles/<名称>/storage_state.json`(**含 session cookie**),重开同 profile 时自动恢复——解决"扫码/快速登录只种 session cookie、关闭浏览器即失效"的问题

典型登录流程:

```
world_open(url, profile="login-淘宝", headful=true)   # 弹窗
  → agent 检测到登录页,提示你完成登录
  → 你在窗口里登录
  → agent 世界模型同步到登录后状态,继续任务
  → world_close 自动导出会话
  → 以后同 profile 打开,免登录(无需再弹窗)
```

注意:登录/验证码场景**必须弹窗**(headful),否则无人能替你完成人工环节。部分网站(如闲鱼)会检测 headless 指纹并拒绝访问("非法访问"提示),headful 模式可规避。

## 行动层策略(locator 优先,双重降级)

操作类工具(world_click/fill/press)采用三层策略,兼顾"原生能力"与"世界模型兜底":

1. **Playwright locator**(默认):根据世界模型元素信息(页面 id → placeholder → ARIA role+可访问名 → 文本)自动构建语义定位,自带自动等待、可见性检查、清晰错误诊断
2. **坐标鼠标手势**(降级):locator 失败时,用世界模型实时 rect + scrollIntoView + 真实鼠标事件
3. **JS 注入**(fill 再降级):React 受控组件 setter + 覆盖层自动切换

工具返回的 `method` 字段标明实际走哪条路径(`locator` / `mouse-gesture` / `js-setter`),agent 可据此判断可靠性。

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
├── test_official.py   # 官方客户端测试
├── screenshots/       # world_screenshot 输出目录
└── README.md          # 本文件
```

## 依赖说明

- 读取 `../agent-runtime-extension-v1.1-blueprint/all-in-one.js` 作为注入内核
- headless Chromium,每次 world_open 一个独立浏览器实例(并行不串扰)
- 截图保存本地,不随 MCP 传输图片(节省上下文,路径返回给 agent)

## 已知限制

- 页面拒绝注入时(极少数反自动化站点)world_open 会报错
- 登录态需要 cookie 的场景暂不支持(无持久化 profile)
- 每个世界约 200MB 内存,并行数量受机器内存限制
- 点击/填表采用真实鼠标手势 + React 兼容 setter(对 Google Flights 等重型组件实测有效)

## 测试脚本

```bash
python test_official.py   # 官方客户端全链路:握手/工具列表/world_open
python test_click.py      # 点击乘客按钮 -> 面板弹出 -> Adults 元素出现
python test_fill.py       # 填出发地 Tokyo -> 建议列表(option.tokyo-japan)出现
python test_action_layer.py  # 行动层:locator 优先策略 + world_press
python test_value.py      # value 入图:fill 后世界模型可见输入框值
python test_profile.py    # headful + profile:登录态持久化验证
```