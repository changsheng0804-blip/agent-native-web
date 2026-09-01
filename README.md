# Agent Web Suite

Agent Web Suite 是一个“网页结构化感知 + MCP 操作 + 操作结果验证”的智能体网页工具套件。

它把网页转换成带有编号、语义名称、坐标、区域、邻居关系和变更记录的结构化空间，让智能体能够：

```text
打开网页 → 查询构件 → 执行动作 → 验证是否生效
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

当前活动版本为 `extension/` + `mcp/`。旧版扩展已移除活动目录，历史版本保留在 Git 提交记录中。项目仍处于实验性阶段，真实网站可能受到登录、动态渲染和反爬机制影响。
