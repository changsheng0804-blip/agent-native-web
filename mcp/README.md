# MCP 服务器

这里是 Agent-Native Web 的 MCP 通道。完整安装、工具说明、测试顺序和安全边界请先阅读根目录的 [项目指南](../docs/项目指南与架构.md)。

## 运行

```bash
pip install mcp playwright
playwright install chromium
python server.py
```

服务器使用标准输入输出模式，由 MCP 客户端启动。服务器会读取 `../extension/all-in-one.js` 作为网页注入内核。

## 本地验证

```bash
python test_enhancements.py
python test_status.py
python test_ipi_filter.py
python test_wait_event.py
python test_map.py
python test_map_drill.py
```

真实网站探针包括 `probe_site.py`、`probe_fill.py` 和 `validate_closed_loop.py`。测试结果记录在 `validate_report.md`。

## 最小实时反馈闭环

当前已经拆出三条可独立读取的页面信道：

```text
world_state          当前最新页面状态
world_change_digest  压缩后的页面变化摘要
world_evidence       动作前后的操作证据
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
```
