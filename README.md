# Agent-Native Web

Agent-Native Web（智能体原生网页）让智能体可以在网页世界中获得快速、准确、无歧义的反馈。

它把网页的空间、语义、关系和实时状态暴露给 AI 智能体，让智能体不再主要依赖截图猜测、DOM 原始噪声或延时轮询：

```text
快速导览页面 → 按需找到目标 → 执行动作 → 立即获得真实反馈
```

**默认协议只有 6 个词(弱模型友好):** `open → guide → find → act → outcome → close`。
每次 `world_act` 都返回同一张统一后果卡(`page_outcome` 五态:progressed/challenged/errored/uncertain/unchanged),
弱模型不再需要自行合并多条信道。其余 `world_*` 工具全部保留并标记为 `[内部/调试]`,
可用 `AGENT_WORLD_LITE=1` 启动只暴露 6 词的精简模式。

## 快速入口

- [完整项目指南](docs/项目指南与架构.md)
- [MCP 服务器](mcp/server.py)
- [站点业务适配器](mcp/site_adapters/)
- [真实站点任务图闭环验证报告](docs/真实站点任务图闭环验证 GitHub.md)
- [真实站点任务图 A/B 对照报告](docs/A-B对照基准 GitHub.md)
- [浏览器扩展内核](extension/)
- [智能体技能包](skills/agent-world/SKILL.md)
- [测试夹具](tests/fixtures/)
- [安全边界说明](docs/安全边界-IPI防御.md)
- [真实网站操作对比记录](docs/真实网站操作对比记录.md)
- [实时反馈与网页导览实施计划](docs/实施计划-实时反馈与网页导览.md)

## 快速运行

```bash
pip install mcp playwright
playwright install chromium
python mcp/server.py
```

`server.py` 使用标准输入输出模式运行，通常由 MCP 客户端自动启动，不建议直接在终端中手动操作。

## 测试与质量门禁(何时跑什么)

测试分三层,不是每次改动都要跑全量:

| 层 | 内容 | 耗时 | 何时跑 |
|---|---|---|---|
| **offline** | 本地夹具 19 项(快、稳定、不联网) | 串行约 14 分钟 / 并行 ×3 约 5 分钟 | 改代码后、提交合并前 |
| **real** | 真实网站 18 项(GitHub/闲鱼等) | 慢,受网络/反爬影响 | 只做真站功能验证时 |
| **special** | 特殊环境(CDP/有头窗口) | — | 按需手动 |

日常节奏(建议规矩):

```bash
# 改文档/说明 → 不用跑测试
# 改某个工具逻辑 → 只跑相关守护面(最快)
python mcp/run_quality.py --scope fill            # 只跑填表相关
python mcp/run_quality.py --scope judgment,challenge
# 只跑一个脚本
python mcp/run_quality.py --only test_protocol.py
# 提交合并前 → 全量 offline(必跑,全绿才合)
python mcp/run_quality.py                          # 串行,约 13 分钟
python mcp/run_quality.py --parallel 3             # 并行,约 5-6 分钟(内存够用建议 3)
# 查看全部守护面与别名
python mcp/run_quality.py --list
```

**门禁纪律**:offline 全量必须全绿;`validate_closed_loop` 的 FP=0 一票否决(把"没生效"误报成"成功"即失败);真实网站测试失败不一定是代码问题(网络/反爬),需人工判断。

## 项目核心

项目最初的重要目标是降低浏览器操作的视觉读取和上下文消耗；随着模型成本下降，当前更核心的问题变成了：复杂网页操作慢、定位难、反馈延迟，并容易让智能体把“没有反馈”误判成“已经成功”。Agent-Native Web 围绕网页导览图、实时变化流和操作生效报告，减少这种猜测空间。

## 当前状态

当前活动版本为 `extension/` + `mcp/`。`world_*` 是为兼容现有客户端保留的 MCP 工具名称。项目仍处于实验性阶段，真实网站可能受到登录、动态渲染和反爬机制影响。
