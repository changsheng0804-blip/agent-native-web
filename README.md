# Agent-Native Web

Agent-Native Web（智能体原生网页）让智能体可以在网页世界中获得快速、准确、无歧义的反馈。

它把网页的空间、语义、关系和实时状态暴露给 AI 智能体，让智能体不再主要依赖截图猜测、DOM 原始噪声或延时轮询：

```text
快速导览页面 → 按需找到目标 → 执行动作 → 立即获得真实反馈
```

## 快速入口

- [完整项目指南](docs/项目指南与架构.md)
- [MCP 服务器](mcp/server.py)
- [浏览器扩展内核](extension/)
- [智能体技能包](skills/agent-world/SKILL.md)
- [测试夹具](tests/fixtures/)
- [安全边界说明](docs/安全边界-IPI防御.md)
- [真实网站操作对比记录](docs/真实网站操作对比记录.md)

## 快速运行

```bash
pip install mcp playwright
playwright install chromium
python mcp/server.py
```

`server.py` 使用标准输入输出模式运行，通常由 MCP 客户端自动启动，不建议直接在终端中手动操作。

## 项目核心

项目最初的重要目标是降低浏览器操作的视觉读取和上下文消耗；随着模型成本下降，当前更核心的问题变成了：复杂网页操作慢、定位难、反馈延迟，并容易让智能体把“没有反馈”误判成“已经成功”。Agent-Native Web 围绕网页导览图、实时变化流和操作生效报告，减少这种猜测空间。

## 当前状态

当前活动版本为 `extension/` + `mcp/`。`world_*` 是为兼容现有客户端保留的 MCP 工具名称。项目仍处于实验性阶段，真实网站可能受到登录、动态渲染和反爬机制影响。
