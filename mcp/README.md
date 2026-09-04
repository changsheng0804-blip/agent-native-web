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

## 任务运行时图

任务图相关信道把页面动作提升为可审计的业务运行时记录：

```text
world_business_state       业务状态投影
world_operation_check      操作前置条件检查
world_task_plan            当前或历史轨迹上的路径规划
world_graph_replay_check   实际轨迹与任务图边的回放核对
world_adapter_compare      站点适配器版本兼容性检查
```

站点适配器 JSON 只能从 `site_adapters/` 受控目录加载。它是显式业务配置，包含状态规则、操作契约和适用版本，不会自动读取或推断网站后端逻辑。

GitHub 真实流程闭环可运行：

```bash
python test_real_github_task_graph.py
```

验证记录见 [真实站点任务图闭环验证报告](../docs/真实站点任务图闭环验证 GitHub.md)。该测试只读公开页面，轨迹写入临时目录并在结束后清理。

GitHub 任务图 A/B 对照可运行：

```bash
python test_real_github_task_graph_ab.py
```

对照记录见 [真实站点任务图 A/B 对照报告](../docs/A-B对照基准 GitHub.md)。A 组每次重新探索页面，B 组先读取任务图再规划；两组动作相同，重点观察复用、安全和执行成本。
