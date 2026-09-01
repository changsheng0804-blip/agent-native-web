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
