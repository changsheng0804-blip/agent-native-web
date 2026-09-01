# Agent-Native Web

Agent-Native Web（智能体原生网页）让网页成为智能体可以直接进入和理解的世界。

它把网页的空间、语义、关系和实时状态暴露给 AI 智能体，让智能体不再依赖截图猜测或反复读取原始页面结构：

```text
进入网页世界 → 读取结构与状态 → 直接行动 → 持续感知变化
```

## 快速入口

- [完整项目指南](docs/项目指南与架构.md)
- [MCP 服务器](mcp/server.py)
- [浏览器扩展内核](extension/)
- [智能体技能包](skills/agent-world/SKILL.md)
- [测试夹具](tests/fixtures/)
- [安全边界说明](docs/安全边界-IPI防御.md)

## 快速运行

```bash
pip install mcp playwright
playwright install chromium
python mcp/server.py
```

`server.py` 使用标准输入输出模式运行，通常由 MCP 客户端自动启动，不建议直接在终端中手动操作。

## 当前状态

当前活动版本为 `extension/` + `mcp/`。`world_*` 是为兼容现有客户端保留的 MCP 工具名称。项目仍处于实验性阶段，真实网站可能受到登录、动态渲染和反爬机制影响。
