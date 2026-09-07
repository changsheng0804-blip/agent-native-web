# MCP 服务器

这里是 Agent-Native Web 的 MCP 通道。完整安装、工具说明、测试顺序和安全边界请先阅读根目录的 [项目指南](../docs/项目指南与架构.md)。

## 运行

```bash
pip install mcp playwright pillow
playwright install chromium
python server.py
```

> ⚠️ `pillow` 是必须依赖(PR #2 起用于视觉 diff 与 Set-of-Mark 标注绘图);缺失会导致 server.py 启动即崩溃。

服务器使用标准输入输出模式，由 MCP 客户端启动。服务器会读取 `../extension/all-in-one.js` 作为网页注入内核。

## 本地验证

```bash
python run_quality.py           # 质检流水线(推荐入口,自动跑前置检查+离线组)
python run_quality.py --real    # 离线 + 真实网站全量
```

真实网站探针包括 `probe_site.py`、`probe_fill.py` 和 `validate_closed_loop.py`。测试结果记录在 `validate_report.md`。

## 最小实时反馈闭环

当前已经拆出三条可独立读取的页面信道：

```text
world_state          当前最新页面状态
world_change_digest  压缩后的页面变化摘要
world_evidence       动作前后的操作证据
world_guide          根据当前任务生成短导览
```

`world_click` 除了返回点击目标附近的局部效果，还会附带 `feedback` 页面整体反馈：

```text
feedback.page        页面前后网址、标题和稳定状态
feedback.overlays    新增或消失的弹窗/菜单
feedback.changes_seq 页面变化序号前后值
```

如果点击后网址已经变化，或页面整体出现新的弹窗/菜单，`world_click` 会优先把结果判定为已生效，避免“页面已经跳转但局部区域没有变化”的误判。回归验证可运行：

```bash
python test_global_feedback.py
python test_channels.py
python test_guide.py
```

## 统一环境侧时间线(world_timeline)

在 L2 前提监视与 L3 动作证据卡之上，`world_timeline` 把三层反馈合并为**同一条环境侧因果时间线**：
动作生命周期(`action`)、网络事件(`request`/`response`/`requestfailed`)、DOM 变化(`dom`)按统一序号
`seq` 与墙钟 `t` 排列，游标增量读取不重不漏。

```text
seq=10 action    world_click start(el_79)
seq=11 request   github.com/git/git/pulls
seq=12 response  200 text/html
...
seq=34 action    world_click start
seq=35 request   api.github.com/pull_request_review_decisions
seq=36 response  200 json          ← 因果窗口 key 归因
```

- 读模式：`mode=digest`(默认)返回聚合摘要；`mode=raw` 返回原始事件明细
- 游标增量：`since=上次 cursor` 不重不漏；环形缓冲 `deque(600)`，随世界销毁清除
- **因果窗口**：每个动作到下一动作之间的全部事件 = 该动作的后果（`counts`/`statuses`/`key`），
  key 只保留高价值项（前提失效、失败、4xx、JSON 接口响应）
- **前提失效**：`premise` 事件带 `derived_from`，指向触发它的响应 `seq`（可回放归因）
- **静默失败**：窗口内出现 4xx/5xx/failed 且无任何 DOM 变化时自动标注 `silent_failures`（L1 盲区）

降噪三规则（真实站点实测 600→38 条，-94%）：初始快照不入账（只建立 name 集合）；批量替换
（同批 add+remove>50，即导航/刷新）聚合为一条 `bulk` 事件；`update` 按"新 name"入账
（渐进扫描逐元素 touch 跳过，真实文本变化如价格 800→1200 必然产生新 name）。

验证与演示：

```bash
python experiments/test_server_timeline.py   # 7 项集成断言
python experiments/run_timeline_gh.py        # GitHub 真实站点演示
```
